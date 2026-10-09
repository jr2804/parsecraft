"""Public analysis entry points: media classification and the canonical analyzer.

Single source of truth shared by the CLI, the benchmark harness, and
external consumers (e.g. the KNOX adapter): ``MEDIA_TYPES``,
``media_type_for``, ``choose_analyzer``, ``analyze_source``. Nothing here
imports CLI code — the CLI maps these typed errors onto its own exit codes.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from pathlib import Path

from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendConfig,
    BackendDescriptor,
    SourceDocument,
)
from parsecraft.backends.registry import BackendRegistry
from parsecraft.ir.models import Diagnostic, DiagnosticLevel
from parsecraft.routing.classifier import (
    CLASSIFIER_PROVENANCE_CODE,
    ClassifierError,
    OcrFacts,
    PageOcrClassifier,
)
from parsecraft.routing.rules import FEATURE_TABLE_CODE

#: Suffix → MIME for every source class this package can consume. Values
#: must be claimed by at least one registered backend (invariant-tested);
#: suffixes with no claiming backend (csv/json/xml) stay unsupported on purpose.
MEDIA_TYPES: dict[str, str] = {
    ".c": "text/plain",
    ".cc": "text/plain",
    ".cfg": "text/plain",
    ".cpp": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".epub": "application/epub+zip",
    ".h": "text/plain",
    ".htm": "text/html",
    ".html": "text/html",
    ".hpp": "text/plain",
    ".ini": "text/plain",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".log": "text/plain",
    ".m": "text/plain",
    ".markdown": "text/markdown",
    ".md": "text/markdown",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".py": "text/plain",
    ".rst": "text/plain",
    ".rtf": "application/rtf",
    ".sh": "text/plain",
    ".tex": "text/plain",
    ".text": "text/plain",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".toml": "text/plain",
    ".txt": "text/plain",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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


def analyze_source(
    source: SourceDocument,
    registry: BackendRegistry,
    *,
    media_type: str,
    installed_extras: Collection[str] | None = None,
    classifier: PageOcrClassifier | None = None,
    offline: bool = False,
) -> AnalysisResult:
    """Analyze ``source`` with the canonical analyzer for ``media_type``.

    Selection and backend failures surface as :class:`NoAnalyzerError` or
    :class:`BackendError` — the CLI maps them onto its exit codes.
    ``installed_extras`` threads through to :func:`choose_analyzer`.

    ``offline`` (default ``False``) is the host's declared offline state, passed
    to the analyzer's factory as ``options["offline"]``: an analyzer that must
    acquire a model refuses the uncached download instead of fetching it
    (pc-e38). It is a plain fact from the probe, never a second declaration.

    ``classifier`` (default ``None`` = exactly today's behaviour) folds
    per-page OCR-need facts into the analysis; a seam :class:`ClassifierError`
    falls back to the unmodified analysis (ADR-0004 A3).
    """
    descriptor = choose_analyzer(registry.list_backends(), media_type, installed_extras=installed_extras)
    backend = registry.create(descriptor.name, BackendConfig(name=descriptor.name, options={"offline": offline}))
    try:
        analysis = backend.analyze(source)
    finally:
        del backend  # one instance at a time — 8 GB VRAM ceiling
    if classifier is None:
        return analysis
    try:
        facts = classifier.classify(source)
    except ClassifierError:
        return analysis  # rule-table fallback, never the judge (ADR-0004 A3)
    return apply_classifier(analysis, facts)


def apply_classifier(analysis: AnalysisResult, facts: OcrFacts) -> AnalysisResult:
    """Merge classifier OCR-need facts into an analysis (augment-only, pure).

    ``classifier_needs_ocr`` is set to ``True`` only for pages the classifier
    flagged; every other page keeps its value. The provenance diagnostic is
    always appended, and ``feature:tables`` is emitted when the classifier
    reported table pages. ``feature:figures`` is never synthesized (ADR-0004
    A5/A6). The input analysis is not mutated.
    """
    signals = [
        signal.model_copy(update={"classifier_needs_ocr": True}) if signal.page_number in facts.pages_needing_ocr else signal for signal in analysis.signals
    ]
    diagnostics = [*analysis.diagnostics, _provenance_diagnostic(facts)]
    if facts.pages_with_tables:
        diagnostics.append(_tables_diagnostic(facts.pages_with_tables))
    return analysis.model_copy(update={"signals": signals, "diagnostics": diagnostics})


def _provenance_diagnostic(facts: OcrFacts) -> Diagnostic:
    """Document-level provenance: classifier source, pdf_type, confidence (A5)."""
    message = f"classifier {facts.source}"
    if facts.pdf_type is not None:
        message = f"{message}; pdf_type={facts.pdf_type}"
    if facts.confidence is not None:
        message = f"{message}; confidence={facts.confidence:.3f}"
    return Diagnostic(level=DiagnosticLevel.INFO, code=CLASSIFIER_PROVENANCE_CODE, message=message)


def _tables_diagnostic(pages: frozenset[int]) -> Diagnostic:
    """``feature:tables`` hint from the classifier's table pages (A6)."""
    ordered = ", ".join(str(page) for page in sorted(pages))
    return Diagnostic(level=DiagnosticLevel.INFO, code=FEATURE_TABLE_CODE, message=f"classifier reports tables on pages {ordered}")


def choose_analyzer(
    descriptors: Sequence[BackendDescriptor],
    media_type: str,
    *,
    installed_extras: Collection[str] | None = None,
) -> BackendDescriptor:
    """Deterministic analyzer: native backends first, then name order.

    When ``installed_extras`` is provided (the caller's set — this module
    never probes the environment), claimers whose optional dependency group
    is installed — or dependency-free — win; only if none is available do we
    fall back to the full candidate list under the same ordering. ``None``
    keeps the historical behaviour exactly. Error types are unchanged.
    """
    candidates = [descriptor for descriptor in descriptors if media_type in descriptor.capabilities.supported_formats]
    if not candidates:
        raise NoAnalyzerError(media_type)
    if installed_extras is None:
        return sorted(candidates, key=_analyzer_key)[0]
    available = [descriptor for descriptor in candidates if _extra_available(descriptor, installed_extras)]
    pool = available if available else candidates
    return sorted(pool, key=_analyzer_key)[0]


def _analyzer_key(descriptor: BackendDescriptor) -> tuple[bool, str]:
    """Native backends first, then stable name order."""
    return (not descriptor.name.startswith("native-"), descriptor.name)


def _extra_available(descriptor: BackendDescriptor, installed_extras: Collection[str]) -> bool:
    group = descriptor.capabilities.optional_dependency_group
    return group is None or group in installed_extras
