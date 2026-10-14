"""PyMuPDF backend — pypdf inspection, PyMuPDF extraction (ADR-0003, ADR-0009).

``analyze()`` uses pypdf (core dependency since ADR-0009) so structure
analysis, encryption detection, and difficulty signals work without the
AGPL PyMuPDF extra. ``convert()`` extracts text with PyMuPDF (``pymupdf``
extra), loaded via ``import_module`` at conversion time; a missing extra
yields a typed ``DEPENDENCY_MISSING`` pass failure, never an import crash.
The descriptor is base-install — registered without an
``optional_dependency_group`` — because analysis is core; extraction
requires ``parsecraft[pymupdf]`` and surfaces a typed failure if it is absent.
"""

from __future__ import annotations

import hashlib
from importlib import import_module
from typing import Protocol, cast, override

from parsecraft.backends.native._common import (
    NATIVE_BACKEND_VERSION,
    DependencyUnavailableError,
    NativeBackendBase,
    PdfInspection,
    source_bytes,
)
from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    BackendFactory,
    DocumentBackend,
    SourceDocument,
)
from parsecraft.backends.pymupdf.code_layout import PageText, build_chunks
from parsecraft.ir.models import Diagnostic, DiagnosticLevel, PageResult, PageSignal

BACKEND_NAME = "pymupdf"
#: PDF plus the two office formats PyMuPDF's own importers read. XLSX is
#: deliberately absent: pymupdf 1.28.2 keeps only numeric cells, dropping
#: strings, so a spreadsheet backend belongs on openpyxl instead (pc-vtw).
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
SUPPORTED_FORMATS = ["application/pdf", _DOCX, _PPTX]

#: The office media types PyMuPDF's importers read. Explicit rather than
#: "anything that is not PDF": a source with no declared media type keeps the
#: PDF route, which is what every existing fixture does.
_OFFICE_MEDIA_TYPES = frozenset({_DOCX, _PPTX})

#: Media type -> the ``filetype`` PyMuPDF needs for its stream opener. The
#: office importers cannot sniff an extension off a stream, so the caller must
#: name the format; PDF is the default because every other format is office.
_PYMUPDF_FILETYPES: dict[str, str] = {
    "application/pdf": "pdf",
    _DOCX: "docx",
    _PPTX: "pptx",
}

_PYMUPDF_ANALYZE_MODULE = "parsecraft.backends.pymupdf._analyze"
_PYMUPDF_CONVERT_MODULE = "parsecraft.backends.pymupdf._convert"


class _PymupdfAnalyzeImpl(Protocol):
    """What this backend needs from the pypdf inspection module."""

    def inspect(self, data: bytes) -> PdfInspection: ...


class _PymupdfConvertImpl(Protocol):
    """What this backend needs from the PyMuPDF extraction module."""

    def page_layouts(self, data: bytes, *, filetype: str = "pdf") -> list[PageText]: ...


class PymupdfBackend(NativeBackendBase):
    """PDF via pypdf/PyMuPDF; DOCX and PPTX via PyMuPDF's office importers.

    Office analysis is the base-install size proxy (no pypdf, no PyMuPDF), so
    the declared surface stays analyzable without the ``pdf`` extra; only
    conversion crosses that boundary, for every format here.
    """

    name = BACKEND_NAME

    def __init__(self, config: BackendConfig, capabilities: BackendCapabilities) -> None:
        super().__init__(config, capabilities, extract=_extract_pages)

    @override
    def analyze(self, source: SourceDocument) -> AnalysisResult:
        data = source_bytes(source)
        if (source.media_type or "") in _OFFICE_MEDIA_TYPES:
            return _office_analysis(data)
        inspection = _analyze_impl().inspect(data)
        signals = [
            PageSignal(
                page_number=stats.page_number,
                has_native_text=not stats.blank,
                text_chars=stats.text_chars,
                image_count=stats.image_count,
                blank=stats.blank,
                replacement_char_ratio=stats.replacement_char_ratio,
            )
            for stats in inspection.pages
        ]
        diagnostics: list[Diagnostic] = []
        if inspection.encrypted:
            diagnostics.append(
                Diagnostic(
                    level=DiagnosticLevel.WARNING,
                    code="pdf-encrypted",
                    message=(
                        "encrypted PDF; page count unavailable without a password"
                        if not inspection.readable
                        else "encrypted PDF; decrypted with an empty password"
                    ),
                )
            )
        if inspection.readable and inspection.page_count > 0 and all(stats.blank for stats in inspection.pages):
            diagnostics.append(
                Diagnostic(
                    level=DiagnosticLevel.INFO,
                    code="pdf-no-extractable-text",
                    message="no extractable text on any page; an OCR pass would be needed",
                )
            )
        return AnalysisResult(
            source_hash=hashlib.sha256(data).hexdigest(),
            page_count=inspection.page_count,
            signals=signals,
            diagnostics=diagnostics,
        )


class _PymupdfFactory:
    """Light entry-point object: loads pypdf inspection at instantiation."""

    descriptor = BackendDescriptor(
        name=BACKEND_NAME,
        version=NATIVE_BACKEND_VERSION,
        capabilities=BackendCapabilities(
            supported_formats=SUPPORTED_FORMATS,
            supports_page_ranges=True,
            supports_multi_page=True,
            convert_extra="pymupdf",
        ),
    )

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        _analyze_impl()  # fail fast if the install is broken
        return PymupdfBackend(config, self.descriptor.capabilities)


def _analyze_impl() -> _PymupdfAnalyzeImpl:
    return cast(_PymupdfAnalyzeImpl, import_module(_PYMUPDF_ANALYZE_MODULE))


def _extract_pages(source: SourceDocument, data: bytes) -> list[PageResult]:
    """PDF and office alike: PyMuPDF's stream opener, told which format it is.

    The route is the same for both — measured 2026-10-13 against real
    python-docx/python-pptx files: ``open(stream=...)`` returns byte-identical
    pages to the temp-file route, so no file-path handler is needed. An earlier
    finding that the stream route fails came from a hand-built OOXML package,
    where BOTH routes fail because mupdf rejects the fabricated file.
    """
    filetype = _PYMUPDF_FILETYPES.get(source.media_type or "", "pdf")
    try:
        impl = cast(_PymupdfConvertImpl, import_module(_PYMUPDF_CONVERT_MODULE))
    except ImportError as exc:
        raise DependencyUnavailableError(_PYMUPDF_CONVERT_MODULE, "pymupdf") from exc
    pages: list[PageResult] = []
    for number, page in enumerate(impl.page_layouts(data, filetype=filetype), start=1):
        layout = build_chunks(page, page_number=number)
        pages.append(PageResult(page_number=number, blocks=layout.chunks, diagnostics=layout.diagnostics))
    return pages


def _office_analysis(data: bytes) -> AnalysisResult:
    """The size proxy every non-PDF backend uses — no pypdf, no PyMuPDF.

    Office analysis must not need the ``pdf`` extra: the declared surface stays
    analyzable on a base install, so the page facts are the format-independent
    ones (one page, bytes-as-text-length). ``pandoc/_impl.py::analyze`` and
    ``native/_common.py::NativeBackendBase.analyze`` are the precedent; this
    mirrors their shape rather than reusing it because this backend already
    overrides ``analyze`` for the PDF branch.
    """
    return AnalysisResult(
        source_hash=hashlib.sha256(data).hexdigest(),
        page_count=1,
        signals=[
            PageSignal(
                page_number=1,
                has_native_text=bool(data),
                text_chars=len(data),
                image_count=0,
                blank=not data,
                replacement_char_ratio=None,
            )
        ],
    )


factory: BackendFactory = _PymupdfFactory()
