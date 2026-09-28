"""Heavy Docling implementation — ``docling`` imported at top level.

Loaded only from the light factory at instantiation (``importlib``-based,
never an inline import). Conversion-verified against docling 2.130.0 on
2026-09-28 for PDF, HTML, Markdown, and plain text.

Design notes:
- ``analyze()`` stays cheap: PDFs are probed with ``pypdfium2`` (a docling
  dependency), text formats are measured directly — no models are loaded and
  nothing is downloaded.
- ``convert()`` runs one ``DocumentConverter`` pass over the requested
  ``page_range`` (docling accepts it on ``convert``), maps docling items to
  typed IR chunks per page, and enforces cancellation, the timeout deadline,
  and the output budget between items. A document that yields no chunks still
  returns the requested pages as empty ``PageResult``s so the executor's
  page-number check holds.
"""

from __future__ import annotations

import hashlib
import os
from io import BytesIO
from pathlib import Path
from time import monotonic
from urllib.parse import unquote, urlparse

import pypdfium2 as pdfium  # ty: ignore[unresolved-import] — extra not installed in dev/CI; heavy by contract
from docling.datamodel.document import ConversionResult  # ty: ignore[unresolved-import]
from docling.document_converter import DocumentConverter  # ty: ignore[unresolved-import]
from docling_core.types.doc.document import DoclingDocument, TableItem, TextItem  # ty: ignore[unresolved-import]
from docling_core.types.io import DocumentStream  # ty: ignore[unresolved-import]

from parsecraft.backends.docling.docling import DESCRIPTOR, DOCLING_BACKEND_VERSION
from parsecraft.backends.errors import BackendError
from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendConfig,
    BackendRef,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
    PageSignal,
    SourceDocument,
)
from parsecraft.ir.models import (
    ChunkKind,
    FailureCode,
    PageRange,
    PageResult,
    PassFailure,
    PassKind,
    StructuredChunk,
    utcnow,
)

#: docling item label -> IR chunk kind (default ``PARAGRAPH`` for text items).
_LABEL_KINDS: dict[str, ChunkKind] = {
    "title": ChunkKind.HEADING,
    "section_header": ChunkKind.HEADING,
    "paragraph": ChunkKind.PARAGRAPH,
    "text": ChunkKind.PARAGRAPH,
    "list_item": ChunkKind.LIST,
    "code": ChunkKind.CODE,
    "formula": ChunkKind.FORMULA,
    "table": ChunkKind.TABLE,
    "caption": ChunkKind.CAPTION,
    "page_header": ChunkKind.HEADER,
    "page_footer": ChunkKind.FOOTER,
    "footnote": ChunkKind.UNKNOWN,
    "reference": ChunkKind.UNKNOWN,
    "document_index": ChunkKind.UNKNOWN,
}

_PDF_MEDIA_TYPE = "application/pdf"
_CONVERTER: DocumentConverter | None = None


class DoclingBackend:
    """Instantiated Docling backend: cheap structural analyze + paged convert."""

    name = DESCRIPTOR.name
    capabilities = DESCRIPTOR.capabilities

    def __init__(self, config: BackendConfig) -> None:
        self._config = config

    def convert(self, request: ConversionRequest) -> BackendResult:
        """Bound-checked conversion into typed IR (never raises)."""
        started = monotonic()
        reference = BackendRef(name=self.name, version=DOCLING_BACKEND_VERSION)
        if request.cancellation is not None and request.cancellation():
            return _result(reference, [], [_failure(request, started, FailureCode.CANCELLED, "cancelled before conversion")], started)
        data = source_bytes(request.source)
        try:
            result = _convert(data, request)
        except Exception as exc:  # docling load/parse boundary — typed, never raw
            detail = f"docling conversion failed: {type(exc).__name__}: {exc}"
            return _result(reference, [], [_failure(request, started, FailureCode.BACKEND_ERROR, detail)], started)
        if request.timeout_s is not None and monotonic() - started > request.timeout_s:
            return _result(reference, [], [_failure(request, started, FailureCode.TIMEOUT, "conversion exceeded timeout_s")], started)
        pages, failure = _select(request, result.document, started)
        failures = [] if failure is None else [failure]
        return _result(reference, pages, failures, started)

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        """Cheap per-page signals: page count + native text length, no models."""
        data = source_bytes(source)
        if source.media_type == _PDF_MEDIA_TYPE:
            signals = _pdf_signals(data)
        else:
            text = data.decode("utf-8", errors="replace")
            signals = [
                PageSignal(
                    page_number=1,
                    has_native_text=bool(text.strip()),
                    text_chars=len(text),
                    image_count=0,
                    blank=not text.strip(),
                )
            ]
        return AnalysisResult(
            source_hash=hashlib.sha256(data).hexdigest(),
            page_count=len(signals),
            signals=signals,
        )


def create(config: BackendConfig) -> DocumentBackend:
    """Instantiate the backend — the sanctioned heavy-import boundary."""
    return DoclingBackend(config)


def _pdf_signals(data: bytes) -> list[PageSignal]:
    """Per-page page count and native text length via pypdfium2 (no models)."""
    document = pdfium.PdfDocument(data)
    try:
        signals: list[PageSignal] = []
        for index in range(len(document)):
            page = document[index]
            textpage = page.get_textpage()
            try:
                text = textpage.get_text_range()
            finally:
                textpage.close()
                page.close()
            signals.append(
                PageSignal(
                    page_number=index + 1,
                    has_native_text=bool(text.strip()),
                    text_chars=len(text),
                    image_count=0,
                    blank=not text.strip(),
                )
            )
        return signals or [PageSignal(page_number=1, has_native_text=False, text_chars=0, image_count=0, blank=True)]
    finally:
        document.close()


def _convert(data: bytes, request: ConversionRequest) -> ConversionResult:
    """Run one docling pass; ``page_range`` is passed only for PDFs."""
    uri = request.source.uri
    name = (_local_path(uri) if uri.startswith("file://") else Path(uri)).name or "document"
    stream = DocumentStream(name=name, stream=BytesIO(data))
    if request.source.media_type == _PDF_MEDIA_TYPE and request.page_range is not None:
        return _converter().convert(stream, page_range=(request.page_range.start, request.page_range.end))
    return _converter().convert(stream)


def _converter() -> DocumentConverter:
    """Process-wide converter; docling pipelines load lazily inside it."""
    global _CONVERTER  # noqa: PLW0603 — one converter per process is the docling contract
    if _CONVERTER is None:
        _CONVERTER = DocumentConverter()
    return _CONVERTER


def _select(
    request: ConversionRequest,
    document: DoclingDocument,
    started: float,
) -> tuple[list[PageResult], PassFailure | None]:
    """Map docling items to typed chunks per page, honoring bounds."""
    blocks: dict[int, list[StructuredChunk]] = {}
    used_chars = 0
    for item, _level in document.iterate_items():
        if request.cancellation is not None and request.cancellation():
            return _filled_pages(blocks, request.page_range), _failure(request, started, FailureCode.CANCELLED, "cancelled between items")
        content, kind = _item_content(item)
        if not content:
            continue
        page_number = item.prov[0].page_no if item.prov else 1
        if request.page_range is not None and not (request.page_range.start <= page_number <= request.page_range.end):
            continue
        if request.max_output_chars is not None and used_chars + len(content) > request.max_output_chars:
            return _filled_pages(blocks, request.page_range), _failure(request, started, FailureCode.BUDGET_EXCEEDED, "max_output_chars budget reached")
        used_chars += len(content)
        chunks = blocks.setdefault(page_number, [])
        chunks.append(
            StructuredChunk(
                id=f"docling-{page_number}-b{len(chunks)}",
                kind=kind,
                content=content,
                page_number=page_number,
                reading_order=len(chunks),
            )
        )
    return _filled_pages(blocks, request.page_range), None


def _item_content(item: object) -> tuple[str, ChunkKind]:
    """Text + chunk kind for one docling item (tables export to Markdown)."""
    if isinstance(item, TableItem):
        return (item.export_to_markdown() or "").strip(), ChunkKind.TABLE
    if isinstance(item, TextItem):
        kind = _LABEL_KINDS.get(str(item.label), ChunkKind.PARAGRAPH)
        return (item.text or "").strip(), kind
    return "", ChunkKind.UNKNOWN


def _filled_pages(blocks: dict[int, list[StructuredChunk]], page_range: PageRange | None) -> list[PageResult]:
    """Every requested page, empty when docling produced no chunks for it."""
    numbers = sorted(blocks) or [1] if page_range is None else list(range(page_range.start, page_range.end + 1))
    return [PageResult(page_number=number, blocks=blocks.get(number, [])) for number in numbers]


def _failure(request: ConversionRequest, started: float, code: FailureCode, detail: str) -> PassFailure:
    return PassFailure(
        code=code,
        pass_kind=PassKind.NATIVE,
        page_range=request.page_range,
        backend=DESCRIPTOR.name,
        backend_version=DOCLING_BACKEND_VERSION,
        budget_s=request.timeout_s if request.timeout_s is not None else 0.0,
        elapsed_s=max(monotonic() - started, 0.0),
        detail=detail,
        occurred_at=utcnow(),
    )


def _result(reference: BackendRef, pages: list[PageResult], failures: list[PassFailure], started: float) -> BackendResult:
    return BackendResult(backend=reference, pages=pages, failures=failures, elapsed_s=max(monotonic() - started, 0.0))


def source_bytes(source: SourceDocument) -> bytes:
    """Raw bytes of a source document (``content`` or a local ``file://`` path)."""
    if source.content is not None:
        return source.content
    try:
        return _local_path(source.uri).read_bytes()
    except OSError as exc:
        msg = f"cannot read source {source.uri!r}: {exc}"
        raise BackendError(msg) from exc


def _local_path(uri: str) -> Path:
    """Filesystem path for a ``file://`` URI (strips the Windows drive slash)."""
    path = unquote(urlparse(uri).path)
    if os.name == "nt" and path.startswith("/") and path[2:3] == ":":
        path = path[1:]
    return Path(path)
