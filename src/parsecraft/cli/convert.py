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
from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendConfig,
    BackendDescriptor,
    SourceDocument,
)
from parsecraft.backends.registry import BackendRegistry
from parsecraft.environment import constraints_from_environment, probe_environment
from parsecraft.ir import DocumentResult, to_markdown
from parsecraft.pipeline import execute
from parsecraft.routing import (
    Intent,
    JudgeViolationError,
    NoEligibleBackendError,
    RoutingConstraints,
    RoutingError,
)

#: File suffix -> media type, matching what the built-in descriptors declare.
MEDIA_TYPES: dict[str, str] = {
    ".htm": "text/html",
    ".html": "text/html",
    ".markdown": "text/markdown",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
}

_USAGE_EXIT_CODE = 2


class ConvertError(Exception):
    """A ``convert`` request that cannot be served; carries a CLI exit code."""

    def __init__(self, detail: str, *, exit_code: int = 1) -> None:
        super().__init__(detail)
        self.exit_code = exit_code


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
    media_type = media_type_for(path)
    source = read_source(path, media_type)
    registry = default_registry
    analysis = analyze(registry, source, media_type)
    constraints = build_constraints(media_type, max_passes=max_passes, allow_ocr=allow_ocr)
    judge = PreferredBackendJudge(backend) if backend is not None else None
    try:
        return execute(analysis, registry, constraints, source, judge).document
    except JudgeViolationError as exc:
        raise ConvertError(f"backend {backend!r} is not eligible for this source: {exc}", exit_code=_USAGE_EXIT_CODE) from exc
    except (NoEligibleBackendError, RoutingError) as exc:
        raise ConvertError(f"routing failed: {exc}") from exc


def media_type_for(path: Path) -> str:
    """Media type for a supported source suffix, or a usage error."""
    media_type = MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        supported = ", ".join(sorted(MEDIA_TYPES))
        raise ConvertError(f"unsupported source {path.name!r}; supported suffixes: {supported}", exit_code=_USAGE_EXIT_CODE)
    return media_type


def read_source(path: Path, media_type: str) -> SourceDocument:
    """Read a source file into an in-memory ``SourceDocument``."""
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ConvertError(f"cannot read source {path}: {exc}") from exc
    return SourceDocument(uri=path.absolute().as_uri(), media_type=media_type, content=content)


def analyze(registry: BackendRegistry, source: SourceDocument, media_type: str) -> AnalysisResult:
    """Analyze a source with the deterministic analysis backend."""
    descriptor = analysis_backend(registry.list_backends(), media_type)
    try:
        backend = registry.create(descriptor.name, BackendConfig(name=descriptor.name))
        return backend.analyze(source)
    except BackendError as exc:
        raise ConvertError(f"analysis with {descriptor.name!r} failed: {exc}") from exc


def analysis_backend(descriptors: Sequence[BackendDescriptor], media_type: str) -> BackendDescriptor:
    """Pick the analyzer deterministically: native backends first, then name order."""
    candidates = [descriptor for descriptor in descriptors if media_type in descriptor.capabilities.supported_formats]
    if not candidates:
        raise ConvertError(f"no installed backend can analyze {media_type}", exit_code=_USAGE_EXIT_CODE)
    candidates.sort(key=lambda descriptor: (not descriptor.name.startswith("native-"), descriptor.name))
    return candidates[0]


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
