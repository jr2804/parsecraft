"""``parsecraft convert`` — analyze, route, execute, render the IR.

Auto mode builds host constraints from :mod:`parsecraft.environment`, plans
with :mod:`parsecraft.routing`, and executes through
:mod:`parsecraft.pipeline`. The non-auto path (``--backend``) uses a CLI-owned
judge that leads with the named backend while keeping other eligible candidates
as fallbacks.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from parsecraft.backends import default_registry
from parsecraft.backends.errors import BackendError
from parsecraft.backends.protocol import BackendDescriptor, SourceDocument
from parsecraft.cli.errors import CliError
from parsecraft.environment import constraints_from_environment, probe_environment
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
    NoEligibleBackendError,
    RoutingConstraints,
    RoutingError,
)

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
    max_passes: int = 1,
    allow_ocr: bool | None = None,
) -> DocumentResult:
    """Analyze, plan, and execute a source, returning the aggregated IR."""
    try:
        media_type = media_type_for(path)
    except UnsupportedSourceError as exc:
        raise ConvertError(str(exc), exit_code=_USAGE_EXIT_CODE) from exc
    source = read_source(path, media_type)
    registry = default_registry
    try:
        analyzer = choose_analyzer(registry.list_backends(), media_type)
    except NoAnalyzerError as exc:
        raise ConvertError(str(exc), exit_code=_USAGE_EXIT_CODE) from exc
    try:
        analysis = analyze_source(source, registry, media_type=media_type)
    except BackendError as exc:
        raise ConvertError(f"analysis with {analyzer.name!r} failed: {exc}") from exc
    constraints = build_constraints(media_type, max_passes=max_passes, allow_ocr=allow_ocr)
    judge = PreferredBackendJudge(backend) if backend is not None else None
    try:
        return execute(analysis, registry, constraints, source, judge).document
    except JudgeViolationError as exc:
        raise ConvertError(f"backend {backend!r} is not eligible for this source: {exc}", exit_code=_USAGE_EXIT_CODE) from exc
    except (NoEligibleBackendError, RoutingError) as exc:
        raise ConvertError(f"routing failed: {exc}") from exc


def read_source(path: Path, media_type: str) -> SourceDocument:
    """Read a source file into an in-memory ``SourceDocument``."""
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ConvertError(f"cannot read source {path}: {exc}") from exc
    return SourceDocument(uri=path.absolute().as_uri(), media_type=media_type, content=content)


def build_constraints(media_type: str, *, max_passes: int, allow_ocr: bool | None) -> RoutingConstraints:
    """Distill detected host facts plus plan inputs into routing constraints."""
    return constraints_from_environment(
        probe_environment(),
        formats={media_type},
        allow_ocr=allow_ocr,
        max_passes=max_passes,
    )


def render(document: DocumentResult, *, as_json: bool) -> str:
    """Markdown projection by default; the full IR as JSON on request."""
    if as_json:
        return json.dumps(document.model_dump(mode="json"), indent=2, sort_keys=True)
    return to_markdown(document)
