"""``parsecraft convert`` — analyze, route, execute, render the IR.

Auto mode builds host constraints from :mod:`parsecraft.environment`, plans
with :mod:`parsecraft.routing`, and executes through
:mod:`parsecraft.pipeline`. The non-auto path (``--backend``) uses a CLI-owned
judge that leads with the named backend while keeping other eligible candidates
as fallbacks. Both optional routing seams accept spec strings: ``--judge``
(re-rank eligible candidates) and ``--classifier`` (fold OCR-need facts into the
analysis).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import typer

from parsecraft.assets.errors import AssetError
from parsecraft.backends import default_registry
from parsecraft.backends.errors import BackendError, DependencyUnavailableError, UnsupportedDependencyVersionError
from parsecraft.backends.protocol import BackendDescriptor, SourceDocument
from parsecraft.cache import ConversionCache
from parsecraft.cli.errors import CliError
from parsecraft.environment import EnvironmentInfo, constraints_from_environment, cuda_runtime_note, probe_environment
from parsecraft.ir import DocumentResult, to_markdown
from parsecraft.ir.models import PassFailure
from parsecraft.pipeline import execute, pipeline_failure
from parsecraft.pipeline.analysis import (
    NoAnalyzerError,
    UnsupportedSourceError,
    analyze_source,
    choose_analyzer,
    media_type_for,
)
from parsecraft.routing import (
    Intent,
    JudgeViolationError,
    MachineProfile,
    NoEligibleBackendError,
    PageContext,
    RoutingConstraints,
    RoutingError,
    RoutingJudge,
    RoutingPreference,
)
from parsecraft.routing.classifier import (
    ClassifierError,
    ClassifierSpecError,
    PageOcrClassifier,
    resolve_classifier,
)
from parsecraft.routing.judge_providers import JudgeError, JudgeSpecError, resolve_judge

_USAGE_EXIT_CODE = 2

#: Header of a combined provider-resolution failure (two or more seams failed).
_PROVIDERS_UNAVAILABLE_HEADER = "routing providers unavailable:"


class ConvertError(CliError):
    """A ``convert`` request that cannot be served; carries a CLI exit code."""


class ProviderResolutionError(ConvertError):
    """A provider seam failed to resolve; carries what a combined diagnosis needs.

    ``str(error)`` is exactly what a lone failure prints — ``prefix: body`` for an
    unavailable provider, the bare body for a malformed spec — so the single-seam
    message shape is preserved by construction. The parts are kept separate so the
    aggregate can list ``flag spec: body`` per failure without re-parsing a message:
    ``body`` is the typed provider error's own text, the part that names the missing
    extra.
    """

    def __init__(self, *, flag: str, spec: str, body: str, prefix: str | None = None, exit_code: int = 1) -> None:
        self.flag = flag
        self.spec = spec
        self.body = body
        message = body if prefix is None else f"{prefix}: {body}"
        super().__init__(message, exit_code=exit_code)


class PreferredBackendJudge:
    """Non-auto judge: lead with ``name``, keep other eligible candidates as fallbacks."""

    def __init__(self, name: str) -> None:
        self._name = name

    def rank(
        self,
        intent: Intent,
        candidates: Sequence[BackendDescriptor],
        context: PageContext | None = None,
    ) -> Sequence[str]:
        """Return the preferred backend first, then the remaining eligible names.

        ``context`` is unused: ``--backend`` already names the lead, so the page
        class cannot change the order this judge produces.
        """
        names = [descriptor.name for descriptor in candidates]
        if self._name not in names:
            raise JudgeViolationError(self._name, "not eligible for this page", intent)
        return [self._name, *(name for name in names if name != self._name)]


def convert_source(
    path: Path,
    *,
    backend: str | None = None,
    judge: str | None = None,
    classifier: str | None = None,
    max_passes: int = 1,
    allow_ocr: bool | None = None,
    preference: RoutingPreference = RoutingPreference.BALANCED,
    use_cache: bool = False,
) -> DocumentResult:
    """Analyze, plan, and execute a source, returning the aggregated IR.

    The suffix check runs first, then ONE host probe feeds the analyzer choice,
    the routing constraints, and the seams. ``judge``/``classifier`` are seam
    spec strings (``provider/model[:variant]``) resolved from that probe before
    the source is read: a malformed spec is a usage error (exit 2), an
    unavailable provider is a runtime failure (exit 1). ``backend`` and
    ``judge`` are mutually exclusive — both choose the lead candidate.
    ``preference`` reaches the plan both as a constraint (the judge the planner
    builds itself) and as the resolved judge's ranking axis, so one flag drives
    both paths. All flags default to their neutral values, which is exactly the
    historical behaviour.
    """
    try:
        media_type = media_type_for(path)
    except UnsupportedSourceError as exc:
        raise ConvertError(str(exc), exit_code=_USAGE_EXIT_CODE) from exc
    environment = probe_environment()  # a single probe per invocation, shared by analysis and the seams
    warn_unusable_gpu(environment)
    resolved_classifier, resolved_judge = _resolve_providers(classifier, backend, judge, environment, preference)
    source = read_source(path, media_type)
    registry = default_registry
    try:
        analyzer = choose_analyzer(registry.list_backends(), media_type, installed_extras=environment.installed_extras)
    except NoAnalyzerError as exc:
        raise ConvertError(str(exc), exit_code=_USAGE_EXIT_CODE) from exc
    try:
        analysis = analyze_source(
            source,
            registry,
            media_type=media_type,
            installed_extras=environment.installed_extras,
            classifier=resolved_classifier,
            offline=environment.offline,
        )
    except DependencyUnavailableError as exc:
        raise ConvertError(f"optional dependency missing: {exc}") from exc  # exit 1
    except UnsupportedDependencyVersionError as exc:
        raise ConvertError(f"unsupported dependency version: {exc}") from exc  # exit 1
    except AssetError as exc:
        # A backend that must acquire a model refuses an uncached download while
        # offline; the asset error already names the model (pc-e38).
        raise ConvertError(f"model assets unavailable: {exc}") from exc  # exit 1
    except BackendError as exc:
        raise ConvertError(f"analysis with {analyzer.name!r} failed: {exc}") from exc
    constraints = build_constraints(media_type, max_passes=max_passes, allow_ocr=allow_ocr, environment=environment, preference=preference)
    try:
        cache = ConversionCache() if use_cache else None
        pipeline = execute(analysis, registry, constraints, source, resolved_judge, cache=cache)
    except JudgeViolationError as exc:
        raise ConvertError(f"backend {backend!r} is not eligible for this source: {exc}", exit_code=_USAGE_EXIT_CODE) from exc
    except AssetError as exc:
        raise ConvertError(f"model assets unavailable: {exc}") from exc  # exit 1, same mapping as the analyzer path
    except (NoEligibleBackendError, RoutingError) as exc:
        raise ConvertError(f"routing failed: {exc}") from exc
    failure = pipeline_failure(pipeline.document)
    if failure is not None:
        raise ConvertError(no_content_message(failure, pipeline.document))  # exit 1
    return pipeline.document


def no_content_message(failure: PassFailure, document: DocumentResult) -> str:
    """Diagnosis for a document no page of which converted — never an empty success.

    The failure detail already names what is missing (a backend's
    ``DependencyUnavailableError`` text carries the extra and the command that
    installs it), so it is relayed verbatim rather than re-parsed.
    """
    pages = document.metadata.page_count
    return f"conversion produced no content: every pass failed for all {pages} page(s) ({failure.code.value} in {failure.backend}) — {failure.detail}"


def warn_unusable_gpu(environment: EnvironmentInfo) -> None:
    """Warn once on stderr when a GPU is present but this runtime cannot use it.

    The common shape is a machine with an NVIDIA card and a CPU-only torch
    wheel: routing then excludes every ``GPU_REQUIRED`` backend, and without
    this line the user sees a CPU fallback with no explanation. Silent when
    there is no GPU to use (nothing to recover) and when the GPU works.
    """
    if environment.vram_budget_gb <= 0 or environment.gpu_usable:
        return
    note = cuda_runtime_note() or "the runtime could not use it"
    typer.echo(
        f"warning: {environment.vram_budget_gb:g} GiB GPU detected but unusable: {note} — GPU-only backends are excluded from routing",
        err=True,
    )


def _resolve_providers(
    classifier: str | None,
    backend: str | None,
    judge: str | None,
    environment: EnvironmentInfo,
    preference: RoutingPreference,
) -> tuple[PageOcrClassifier | None, RoutingJudge | None]:
    """Resolve both provider seams, reporting every failure in one diagnosis.

    Each seam is attempted independently, so a command naming two providers with
    two missing extras learns both in one run instead of one install round-trip
    each. A lone failure re-raises its original error untouched — the
    single-provider message shape is a contract — while two or more combine into
    the header plus one line per failure, carrying the usage exit code when any of
    them was a malformed spec (a caller bug still outranks environment state).
    Both seams resolve before the source is read, so the timing is unchanged.
    """
    failures: list[ProviderResolutionError] = []
    resolved_classifier: PageOcrClassifier | None = None
    resolved_judge: RoutingJudge | None = None
    try:
        resolved_classifier = _resolve_classifier(classifier)
    except ProviderResolutionError as exc:
        failures.append(exc)
    try:
        resolved_judge = _resolve_judge(backend, judge, environment, preference)
    except ProviderResolutionError as exc:
        failures.append(exc)
    if len(failures) == 1:
        raise failures[0]
    if failures:
        raise _combined_provider_error(failures)
    return resolved_classifier, resolved_judge


def _combined_provider_error(failures: Sequence[ProviderResolutionError]) -> ConvertError:
    """One diagnosis listing every provider that failed to resolve.

    The per-provider ``body`` — the typed error's own text, which names the
    missing extra — is relayed verbatim, so the combined message adds no new
    wording to re-verify.
    """
    lines = [_PROVIDERS_UNAVAILABLE_HEADER]
    lines.extend(f"  {failure.flag} {failure.spec}: {failure.body}" for failure in failures)
    exit_code = _USAGE_EXIT_CODE if any(failure.exit_code == _USAGE_EXIT_CODE for failure in failures) else 1
    return ConvertError("\n".join(lines), exit_code=exit_code)


def _resolve_classifier(spec: str | None) -> PageOcrClassifier | None:
    """Resolve a classifier spec: malformed is a usage error, unavailable is not."""
    if spec is None:
        return None
    try:
        return resolve_classifier(spec)
    except ClassifierSpecError as exc:
        raise ProviderResolutionError(flag="--classifier", spec=spec, body=str(exc), exit_code=_USAGE_EXIT_CODE) from exc
    except ClassifierError as exc:
        raise ProviderResolutionError(flag="--classifier", spec=spec, body=str(exc), prefix="classifier unavailable") from exc


def _resolve_judge(
    backend: str | None,
    spec: str | None,
    environment: EnvironmentInfo,
    preference: RoutingPreference,
) -> RoutingJudge | None:
    """Resolve the judge seam: ``--backend`` builds the CLI's own lead-candidate judge.

    ``None`` (no seam flag) stays ``None`` so ``execute`` keeps its default
    deterministic judge — byte-identical to the pre-flag behaviour — and that
    judge reads the preference from the constraints. The host facts from
    ``environment`` reach a machine-aware judge, so it can weigh hardware fit
    among the eligible candidates without probing the host again, and
    ``preference`` reaches a preference-aware one; both are ignored by judges
    that have no use for them.
    """
    if backend is not None and spec is not None:
        msg = "--backend and --judge are mutually exclusive: both choose the lead candidate"
        raise ProviderResolutionError(flag="--judge", spec=spec, body=msg, exit_code=_USAGE_EXIT_CODE)
    if spec is None:
        return PreferredBackendJudge(backend) if backend is not None else None
    machine = MachineProfile(vram_budget_gb=environment.vram_budget_gb, gpu_usable=environment.gpu_usable)
    try:
        return resolve_judge(spec, machine=machine, preference=preference)
    except JudgeSpecError as exc:
        raise ProviderResolutionError(flag="--judge", spec=spec, body=str(exc), exit_code=_USAGE_EXIT_CODE) from exc
    except JudgeError as exc:
        raise ProviderResolutionError(flag="--judge", spec=spec, body=str(exc), prefix="judge unavailable") from exc


def read_source(path: Path, media_type: str) -> SourceDocument:
    """Read a source file into an in-memory ``SourceDocument``."""
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ConvertError(f"cannot read source {path}: {exc}") from exc
    return SourceDocument(uri=path.absolute().as_uri(), media_type=media_type, content=content)


def build_constraints(
    media_type: str,
    *,
    max_passes: int,
    allow_ocr: bool | None,
    environment: EnvironmentInfo | None = None,
    preference: RoutingPreference = RoutingPreference.BALANCED,
) -> RoutingConstraints:
    """Distill detected host facts plus plan inputs into routing constraints.

    ``environment`` lets a caller that already probed the host reuse that one
    result; ``None`` probes here (the library default).
    """
    return constraints_from_environment(
        environment if environment is not None else probe_environment(),
        formats={media_type},
        allow_ocr=allow_ocr,
        max_passes=max_passes,
        preference=preference,
    )


def render(document: DocumentResult, *, as_json: bool) -> str:
    """Markdown projection by default; the full IR as JSON on request."""
    if as_json:
        return json.dumps(document.model_dump(mode="json"), indent=2, sort_keys=True)
    return to_markdown(document)
