"""Native PDF backend — pypdf inspection, PyMuPDF extraction (ADR-0003).

``analyze()`` uses pypdf (``pdf-lite`` extra, permissive) so structure
analysis, encryption detection, and difficulty signals work without the
AGPL PyMuPDF extra. ``convert()`` extracts text with PyMuPDF (``pdf``
extra), loaded via ``import_module`` at conversion time; a missing extra
yields a typed ``DEPENDENCY_MISSING`` pass failure, never an import crash.
Descriptor carries ``optional_dependency_group="pdf-lite"`` (the light
prerequisite).
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
from parsecraft.backends.native.code_layout import PageText, build_chunks
from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    BackendFactory,
    DocumentBackend,
    SourceDocument,
)
from parsecraft.ir.models import Diagnostic, DiagnosticLevel, PageResult, PageSignal

BACKEND_NAME = "native-pdf"
SUPPORTED_FORMATS = ["application/pdf"]

_PDF_INSPECT_MODULE = "parsecraft.backends.native.pdf_inspect"
_PDF_TEXT_MODULE = "parsecraft.backends.native.pdf_text"


class _PdfInspectImpl(Protocol):
    """What this backend needs from the pypdf inspection module."""

    def inspect(self, data: bytes) -> PdfInspection: ...


class _PdfTextImpl(Protocol):
    """What this backend needs from the PyMuPDF extraction module."""

    def page_layouts(self, data: bytes) -> list[PageText]: ...


class PdfBackend(NativeBackendBase):
    """PDF: pypdf for analysis, PyMuPDF for text extraction."""

    name = BACKEND_NAME

    def __init__(self, config: BackendConfig, capabilities: BackendCapabilities) -> None:
        super().__init__(config, capabilities, extract=_extract_pdf)

    @override
    def analyze(self, source: SourceDocument) -> AnalysisResult:
        data = source_bytes(source)
        inspection = _inspect_impl().inspect(data)
        signals = [
            PageSignal(
                page_number=stats.page_number,
                has_native_text=not stats.blank,
                text_chars=stats.text_chars,
                image_count=0,
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


class _PdfFactory:
    """Light entry-point object: loads pypdf inspection at instantiation."""

    descriptor = BackendDescriptor(
        name=BACKEND_NAME,
        version=NATIVE_BACKEND_VERSION,
        capabilities=BackendCapabilities(
            supported_formats=SUPPORTED_FORMATS,
            supports_page_ranges=True,
            supports_multi_page=True,
            optional_dependency_group="pdf-lite",
        ),
    )

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        _inspect_impl()  # fail fast when the pdf-lite extra is missing
        return PdfBackend(config, self.descriptor.capabilities)


def _inspect_impl() -> _PdfInspectImpl:
    try:
        return cast(_PdfInspectImpl, import_module(_PDF_INSPECT_MODULE))
    except ImportError as exc:
        raise DependencyUnavailableError(_PDF_INSPECT_MODULE, "pdf-lite") from exc


def _extract_pdf(source: SourceDocument, data: bytes) -> list[PageResult]:
    try:
        impl = cast(_PdfTextImpl, import_module(_PDF_TEXT_MODULE))
    except ImportError as exc:
        raise DependencyUnavailableError(_PDF_TEXT_MODULE, "pdf") from exc
    pages: list[PageResult] = []
    for number, page in enumerate(impl.page_layouts(data), start=1):
        layout = build_chunks(page, page_number=number)
        pages.append(PageResult(page_number=number, blocks=layout.chunks, diagnostics=layout.diagnostics))
    return pages


factory: BackendFactory = _PdfFactory()
