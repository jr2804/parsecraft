"""Public analysis entry points: media classification and the canonical analyzer.

Single source of truth shared by the CLI, the benchmark harness, and
external consumers: ``MEDIA_TYPES``,
``media_type_for``, ``choose_analyzer``, ``analyze_source``. Nothing here
imports CLI code — the CLI maps these typed errors onto its own exit codes.
"""

from __future__ import annotations

import codecs
import re
from collections.abc import Collection, Sequence
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

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
from parsecraft.routing.rules import FEATURE_TABLE_CODE, is_ocr

#: Suffix → MIME for every source class this package can consume. Values
#: must be claimed by at least one registered backend (invariant-tested).
#: Suffixes with no claiming backend (json/xml/extensionless) stay OUT of
#: this map on purpose: the CLI routes them through ``sniff_text_plain``
#: (pc-9p6.14), which maps valid text to ``text/plain``.
MEDIA_TYPES: dict[str, str] = {
    ".c": "text/plain",
    ".cc": "text/plain",
    ".cfg": "text/plain",
    ".cpp": "text/plain",
    ".csv": "text/csv",
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
    ".tsv": "text/tab-separated-values",
    ".txt": "text/plain",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


#: BOM prefix → (exact codec, code unit size); longest prefixes first (pc-9p6.14).
_BOM_CODECS: tuple[tuple[bytes, str, int], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32-le", 4),
    (codecs.BOM_UTF32_BE, "utf-32-be", 4),
    (codecs.BOM_UTF16_LE, "utf-16-le", 2),
    (codecs.BOM_UTF16_BE, "utf-16-be", 2),
)

#: Lone surrogates are not valid Unicode text (pc-9p6.14).
_LONE_SURROGATE_RE = re.compile("[\ud800-\udfff]")

#: BOM validity is checked over at most this many leading bytes (pc-9p6.14).
_BOM_PROBE_BYTES = 65536

_HEAD_WINDOW_BYTES = 32768
_TAIL_WINDOW_BYTES = 32768
_STACKED_WINDOW_BYTES = _HEAD_WINDOW_BYTES + _TAIL_WINDOW_BYTES


class _BinaryCheck(Protocol):
    """The ``binaryornot.check`` surface this module needs (imported call-time)."""

    def is_binary_string(self, data: bytes) -> bool: ...


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


def sniff_text_plain(data: bytes) -> bool:
    """Heuristic gate: unknown-suffix bytes that are probably text (pc-9p6.14).

    A BOM prefix is an exact declaration, not a statistical call — but only
    when the BOM-declared codec strictly decodes the leading bytes with no
    lone surrogates (a lying or truncated prefix falls through to the
    windows below instead of being blindly approved as text). The UTF-8 BOM
    is strict-validated the same way (bounded probe with torn-edge trim;
    small files validate whole). Validity is checked over at most the first
    64 KiB, so the cost stays bounded no matter the file size. Otherwise binaryornot decides over the full
    content up to 64 KiB, else over head+tail 32 KiB windows whose cuts are
    aligned to UTF-8 code-point boundaries first — a cut landing inside a
    multibyte sequence would otherwise read as binary (pc-3 adversarial:
    valid CJK/emoji UTF-8 classified binary on torn slices). Binary confined
    to the unsampled middle is NOT detected — a documented limitation, not
    a security gate. Multibyte text binaryornot itself cannot see (BOM-less
    UTF-16 CJK reads binary even full-scan) is a known false negative,
    documented not fixed. Empty input sniffs as text (parity with empty
    ``.txt``).
    """
    # Call-time import (import_module, never an import statement): the sorter
    # cannot hoist it, so the startup path never pays the ~0.5 s table cost.
    check = _binaryornot_check()

    if _bom_declares_text(data):
        return True
    if len(data) <= _STACKED_WINDOW_BYTES:
        return not check.is_binary_string(data)
    head = _utf8_edge_trimmed_head(data[:_HEAD_WINDOW_BYTES])
    tail = _utf8_edge_trimmed_tail(data[-_TAIL_WINDOW_BYTES:])
    return not check.is_binary_string(head) and not check.is_binary_string(tail)


def _binaryornot_check() -> _BinaryCheck:
    """Import binaryornot at call time: ~0.5 s of tables; analysis.py is startup-path."""
    return cast("_BinaryCheck", import_module("binaryornot.check"))


def _bom_declares_text(data: bytes) -> bool:
    """Whether a BOM prefix strictly validates as text (pc-9p6.14)."""
    for bom, codec, unit in _BOM_CODECS:
        if not data.startswith(bom):
            continue
        if (len(data) - len(bom)) % unit != 0:
            return False  # truncated units: not a well-formed stream, fall through
        probe = data[:_BOM_PROBE_BYTES]
        probe = probe[: len(probe) - (len(probe) % unit)]
        try:
            text = probe.decode(codec)
        except UnicodeDecodeError:
            return False
        return _LONE_SURROGATE_RE.search(text) is None
    if data.startswith(codecs.BOM_UTF8):
        probe = data if len(data) <= _BOM_PROBE_BYTES else _utf8_edge_trimmed_head(data[:_BOM_PROBE_BYTES])
        try:
            probe.decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False
    return False


def _utf8_edge_trimmed_head(window: bytes) -> bytes:
    """Drop a torn multibyte tail (at most 3 bytes) from a head window (pc-9p6.14)."""
    for cut in range(4):
        candidate = window if cut == 0 else window[:-cut]
        try:
            candidate.decode("utf-8")
            return candidate
        except UnicodeDecodeError:
            continue
    return window


def _utf8_edge_trimmed_tail(window: bytes) -> bytes:
    """Drop a torn multibyte lead (at most 3 bytes) from a tail window (pc-9p6.14)."""
    for skip in range(4):
        try:
            window[skip:].decode("utf-8")
            return window[skip:]
        except UnicodeDecodeError:
            continue
    return window


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
    """Deterministic analyzer: base-install backends first, then name order.

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


def _analyzer_key(descriptor: BackendDescriptor) -> tuple[bool, bool, str]:
    """Base-install, non-OCR analyzers first, then in-package before delegated.

    The SECOND element is the pc-s7g constraint (user ruling: "constrain it"): a
    packaging promotion must not decide which parser produces the routing
    signals. Promoting ``liteparse`` and ``pdf-inspector`` from extra to base
    otherwise handed PDFs to ``liteparse`` on name order, because both are
    base-install and neither is an OCR route. In-package inspection
    (``pymupdf``'s own pypdf pass) now outranks a converter's delegated
    ``analyze()``; the pool itself is unchanged, so an image still prefers its
    installed claimer over an OCR route.

    Keyed on what the analyzer contract is about — a base-install backend
    (``optional_dependency_group is None``, so ``analyze()`` is core/base work)
    that is not an OCR route — **not** on a ``native-`` name prefix. The prefix
    key silently inverted when ``native-pdf`` was renamed to ``pymupdf``
    (pc-kwh→pc-hav): ``pymupdf`` lost the prefix, so a PDF was analyzed by the
    alphabetically-first claimer (``docling``), making the analyzer's signal
    source depend on which extras happened to be installed.

    The non-OCR clause is not decoration: ``ocr-tesseract`` is ALSO
    base-install (group ``None``), so base-install alone would hand every PDF
    to the raster OCR engine. OCR routes are the fallback family by design
    (routing's NATIVE-lead rule), never the signal source while a base native
    can analyze the format.
    """
    capabilities = descriptor.capabilities
    base_non_ocr = not is_ocr(descriptor) and capabilities.optional_dependency_group is None
    # ``delegated_analysis`` is the pc-s7g constraint (user ruling: "constrain it"),
    # and it is FIRST because a converter's parser is both not-cheap and not
    # reliable: measured, liteparse rejects a valid 8x8 png where the size proxy
    # cannot. Routing signals are therefore never taken from a delegated parser
    # while a non-delegated analyzer is available for the format — which keeps
    # pymupdf on PDFs and restores the pre-promotion behaviour on images (an OCR
    # backend analyzes). Ordering, not a pool exclusion: a delegated analyzer is
    # still the last resort for a format nothing else claims.
    return (capabilities.delegated_analysis, not base_non_ocr, descriptor.name)


def _extra_available(descriptor: BackendDescriptor, installed_extras: Collection[str]) -> bool:
    group = descriptor.capabilities.optional_dependency_group
    return group is None or group in installed_extras
