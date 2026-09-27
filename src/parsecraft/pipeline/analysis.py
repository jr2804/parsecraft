"""Public analysis entry points: media classification and the canonical analyzer.

Single source of truth shared by the CLI, the benchmark harness, and
external consumers (e.g. the KNOX adapter): ``MEDIA_TYPES``,
``media_type_for``, ``choose_analyzer``, ``analyze_source``. Nothing here
imports CLI code — the CLI maps these typed errors onto its own exit codes.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendConfig,
    BackendDescriptor,
    SourceDocument,
)
from parsecraft.backends.registry import BackendRegistry

#: Suffix → MIME for every source class this package can consume. Values
#: must be claimed by at least one registered backend (invariant-tested);
#: suffixes with no claiming backend (csv/json/xml) stay unsupported on purpose.
MEDIA_TYPES: dict[str, str] = {
    ".c": "text/plain",
    ".cc": "text/plain",
    ".cfg": "text/plain",
    ".cpp": "text/plain",
    ".h": "text/plain",
    ".htm": "text/html",
    ".html": "text/html",
    ".hpp": "text/plain",
    ".ini": "text/plain",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".log": "text/plain",
    ".markdown": "text/markdown",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".py": "text/plain",
    ".rst": "text/plain",
    ".sh": "text/plain",
    ".tex": "text/plain",
    ".text": "text/plain",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".toml": "text/plain",
    ".txt": "text/plain",
}


class AnalysisError(Exception):
    """Base class for public analysis-path failures (never a CLI error)."""


class UnsupportedSourceError(AnalysisError):
    """The source suffix is not in :data:`MEDIA_TYPES`."""

    def __init__(self, source_name: str, supported_suffixes: str) -> None:
        self.source_name = source_name
        self.supported_suffixes = supported_suffixes
        super().__init__(f"unsupported source {source_name!r}; supported suffixes: {supported_suffixes}")


class NoAnalyzerError(AnalysisError):
    """No installed backend claims the document's media type."""

    def __init__(self, media_type: str) -> None:
        self.media_type = media_type
        super().__init__(f"no installed backend can analyze {media_type}")


def media_type_for(path: Path) -> str:
    """MIME for a supported source suffix, or a typed :class:`UnsupportedSourceError`."""
    media_type = MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        supported = ", ".join(sorted(MEDIA_TYPES))
        raise UnsupportedSourceError(path.name, supported)
    return media_type


def analyze_source(source: SourceDocument, registry: BackendRegistry, *, media_type: str) -> AnalysisResult:
    """Analyze ``source`` with the canonical analyzer for ``media_type``.

    Selection and backend failures surface as :class:`NoAnalyzerError` or
    :class:`BackendError` — the CLI maps them onto its exit codes.
    """
    descriptor = choose_analyzer(registry.list_backends(), media_type)
    backend = registry.create(descriptor.name, BackendConfig(name=descriptor.name))
    try:
        return backend.analyze(source)
    finally:
        del backend  # one instance at a time — 8 GB VRAM ceiling


def choose_analyzer(descriptors: Sequence[BackendDescriptor], media_type: str) -> BackendDescriptor:
    """Deterministic analyzer: native backends first, then name order."""
    candidates = [descriptor for descriptor in descriptors if media_type in descriptor.capabilities.supported_formats]
    if not candidates:
        raise NoAnalyzerError(media_type)
    candidates.sort(key=lambda descriptor: (not descriptor.name.startswith("native-"), descriptor.name))
    return candidates[0]
