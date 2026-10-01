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

from parsecraft.backends import default_registry
from parsecraft.backends.errors import BackendError, DependencyUnavailableError, UnsupportedDependencyVersionError
from parsecraft.backends.protocol import BackendDescriptor, SourceDocument
from parsecraft.cache import ConversionCache
from parsecraft.cli.errors import CliError
from parsecraft.environment import EnvironmentInfo, constraints_from_environment, probe_environment
from parsecraft.ir import DocumentResult, to_markdown
from parsecraft.pipeline import execute
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
    RoutingConstraints,
    RoutingError,
    RoutingJudge,
)
from parsecraft.routing.classifier import (
    ClassifierError,
    ClassifierSpecError,
    PageOcrClassifier,
    resolve_classifier,
)
from parsecraft.routing.judge_providers import JudgeError, JudgeSpecError, resolve_judge

_USAGE_EXIT_CODE = 2


class ConvertError(CliError):
    """A ``convert`` request that cannot be served; carries a CLI exit code."""


class PreferredBackendJudge:
    """Non-auto judge: lead with ``name``, keep other eligible candidates as fallbacks."""

    def __init__(self, name: str) -> None:
        self._name = name

    def rank(self, intent: Intent, candidates: Sequence[BackendDescriptor]) -> Sequence[str]:
        """Return the preferred backend first, then the remaining eligible names."""
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
    use_cache: bool = False,
) -> DocumentResult:
    """Analyze, plan, and execute a source, returning the aggregated IR.

    The suffix check runs first, then ONE host probe feeds the analyzer choice,
    the routing constraints, and the seams. ``judge``/``classifier`` are seam
    spec strings (``provider/model[:variant]``) resolved from that probe before
    the source is read: a malformed spec is a usage error (exit 2), an
    unavailable provider is a runtime failure (exit 1). ``backend`` and
    ``judge`` are mutually exclusive — both choose the lead candidate. Both
    flags default to ``None``, which is exactly the historical behaviour.
    """
    try:
        media_type = media_type_for(path)
    except UnsupportedSourceError as exc:
        raise ConvertError(str(exc), exit_code=_USAGE_EXIT_CODE) from exc
    environment = probe_environment()  # a single probe per invocation, shared by analysis and the seams
    resolved_classifier = _resolve_classifier(classifier)
    resolved_judge = _resolve_judge(backend, judge, environment)
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
        )
    except DependencyUnavailableError as exc:
        raise ConvertError(f"optional dependency missing: {exc}") from exc  # exit 1
    except UnsupportedDependencyVersionError as exc:
        raise ConvertError(f"unsupported dependency version: {exc}") from exc  # exit 1
    except BackendError as exc:
        raise ConvertError(f"analysis with {analyzer.name!r} failed: {exc}") from exc
    constraints = build_constraints(media_type, max_passes=max_passes, allow_ocr=allow_ocr, environment=environment)
    try:
        cache = ConversionCache() if use_cache else None
        return execute(analysis, registry, constraints, source, resolved_judge, cache=cache).document
    except JudgeViolationError as exc:
        raise ConvertError(f"backend {backend!r} is not eligible for this source: {exc}", exit_code=_USAGE_EXIT_CODE) from exc
    except (NoEligibleBackendError, RoutingError) as exc:
        raise ConvertError(f"routing failed: {exc}") from exc


def _resolve_classifier(spec: str | None) -> PageOcrClassifier | None:
    """Resolve a classifier spec: malformed is a usage error, unavailable is not."""
    try:
        return resolve_classifier(spec)
    except ClassifierSpecError as exc:
        raise ConvertError(str(exc), exit_code=_USAGE_EXIT_CODE) from exc
    except ClassifierError as exc:
        raise ConvertError(f"classifier unavailable: {exc}") from exc  # exit 1


def _resolve_judge(backend: str | None, spec: str | None, environment: EnvironmentInfo) -> RoutingJudge | None:
    """Resolve the judge seam: ``--backend`` builds the CLI's own lead-candidate judge.

    ``None`` (neither flag) stays ``None`` so ``execute`` keeps its default
    deterministic judge — byte-identical to the pre-flag behaviour. The host
    facts from ``environment`` reach a machine-aware judge, so it can weigh
    hardware fit among the eligible candidates without probing the host again.
    """
    if backend is not None and spec is not None:
        msg = "--backend and --judge are mutually exclusive: both choose the lead candidate"
        raise ConvertError(msg, exit_code=_USAGE_EXIT_CODE)
    if spec is None:
        return PreferredBackendJudge(backend) if backend is not None else None
    machine = MachineProfile(vram_budget_gb=environment.vram_budget_gb)
    try:
        return resolve_judge(spec, machine=machine)
    except JudgeSpecError as exc:
        raise ConvertError(str(exc), exit_code=_USAGE_EXIT_CODE) from exc
    except JudgeError as exc:
        raise ConvertError(f"judge unavailable: {exc}") from exc  # exit 1


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
    )


def render(document: DocumentResult, *, as_json: bool) -> str:
    """Markdown projection by default; the full IR as JSON on request."""
    if as_json:
        return json.dumps(document.model_dump(mode="json"), indent=2, sort_keys=True)
    return to_markdown(document)
