"""Heavy pdf-inspector implementation — ``pdf_inspector`` imported at top level.

Loaded only from the light factory at instantiation (``importlib``-based, never
an inline import). Verified against pdf-inspector 1.25.2 on 2026-10-01.

Design notes:
- ``analyze()`` and ``convert()`` both read pdf-inspector's per-page Markdown
  (``extract_pages_markdown_bytes``, one pass): that call carries the library's
  own per-page ``needs_ocr`` verdict *and* the page's text volume, which is
  exactly what the planner signals and the IR pages need. ``process_pdf``
  returns one unattributeable Markdown blob for the whole document, so it
  cannot back per-page IR pages or a ``page_range``.
- ``convert()`` runs one whole-document pass and filters pages to the requested
  ``page_range`` while mapping (docling's shape), so the document's real page
  count decides whether a range is out of bounds — pdf-inspector answers an
  out-of-range page index with an empty phantom page, which must never reach
  the IR.
- Bounds are honored between pages: cancellation, ``timeout_s`` (checked after
  the single blocking pass), and ``max_output_chars``. Failures stay typed
  (``CANCELLED``, ``TIMEOUT``, ``BUDGET_EXCEEDED``, ``INVALID_INPUT``,
  ``BACKEND_ERROR``), never raw.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from time import monotonic

import pdf_inspector  # ty: ignore[unresolved-import] — extra not installed in dev/CI; heavy by contract

from parsecraft.backends.errors import BackendError
from parsecraft.backends.pdf_inspector.pdf_inspector import DESCRIPTOR, PDF_INSPECTOR_BACKEND_VERSION
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
from parsecraft.backends.source import path_from_file_uri
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

#: U+FFFD, the replacement character a broken font encoding leaves behind.
_REPLACEMENT_CHAR = "\ufffd"


class PdfInspectorBackend:
    """Instantiated pdf-inspector backend: per-page Markdown signals + conversion."""

    name = DESCRIPTOR.name
    capabilities = DESCRIPTOR.capabilities

    def __init__(self, config: BackendConfig) -> None:
        self._config = config

    def convert(self, request: ConversionRequest) -> BackendResult:
        """Bound-checked conversion into typed IR (never raises)."""
        started = monotonic()
        reference = BackendRef(name=self.name, version=PDF_INSPECTOR_BACKEND_VERSION)
        if request.cancellation is not None and request.cancellation():
            return _result(reference, [], [_failure(request, started, FailureCode.CANCELLED, "cancelled before conversion")], started)
        data = source_bytes(request.source)
        try:
            extracted = pdf_inspector.extract_pages_markdown_bytes(data).pages
        except ValueError as exc:  # upstream's invalid-input error (not a PDF, unreadable)
            detail = f"pdf-inspector rejected the source {request.source.uri!r}: {exc}"
            return _result(reference, [], [_failure(request, started, FailureCode.INVALID_INPUT, detail)], started)
        except Exception as exc:  # library boundary — typed, never raw
            detail = f"pdf-inspector conversion failed: {type(exc).__name__}: {exc}"
            return _result(reference, [], [_failure(request, started, FailureCode.BACKEND_ERROR, detail)], started)
        numbers = [page.page + 1 for page in extracted if _in_range(page.page + 1, request.page_range)]
        if not numbers:
            detail = "source has no pages to convert" if request.page_range is None else "requested page range is outside the document"
            return _result(reference, [], [_failure(request, started, FailureCode.INVALID_INPUT, detail)], started)
        if request.timeout_s is not None and monotonic() - started > request.timeout_s:
            return _result(reference, [], [_failure(request, started, FailureCode.TIMEOUT, "conversion exceeded timeout_s")], started)
        pages, failure = _select(request, extracted, numbers, started)
        failures = [] if failure is None else [failure]
        return _result(reference, pages, failures, started)

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        """Cheap per-page signals: pdf-inspector's OCR verdict + Markdown volume."""
        data = source_bytes(source)
        try:
            extracted = pdf_inspector.extract_pages_markdown_bytes(data).pages
        except Exception as exc:  # unsupported/undecodable input → typed, never raw
            msg = f"pdf-inspector cannot inspect source {source.uri!r}: {type(exc).__name__}: {exc}"
            raise BackendError(msg) from exc
        return AnalysisResult(
            source_hash=hashlib.sha256(data).hexdigest(),
            page_count=len(extracted),
            signals=[_signal(page) for page in extracted],
        )


def create(config: BackendConfig) -> DocumentBackend:
    """Instantiate the backend — the sanctioned heavy-import boundary."""
    return PdfInspectorBackend(config)


def _select(
    request: ConversionRequest,
    extracted: list[pdf_inspector.PageMarkdown],
    numbers: list[int],
    started: float,
) -> tuple[list[PageResult], PassFailure | None]:
    """Map the requested pages to typed chunks, honoring cancellation and budget."""
    blocks: dict[int, list[StructuredChunk]] = {}
    used_chars = 0
    for page in extracted:
        number = page.page + 1
        if not _in_range(number, request.page_range):
            continue
        if request.cancellation is not None and request.cancellation():
            return _filled_pages(blocks, numbers), _failure(request, started, FailureCode.CANCELLED, "cancelled between pages")
        content = page.markdown.strip()
        if not content:
            blocks.setdefault(number, [])
            continue
        if request.max_output_chars is not None and used_chars + len(content) > request.max_output_chars:
            return _filled_pages(blocks, numbers), _failure(request, started, FailureCode.BUDGET_EXCEEDED, "max_output_chars budget reached")
        used_chars += len(content)
        blocks[number] = [
            StructuredChunk(
                id=f"pdf-inspector-p{number}-b0",
                kind=ChunkKind.PARAGRAPH,
                content=content,
                page_number=number,
                reading_order=0,
                metadata={"backend": "pdf-inspector"},
            )
        ]
    return _filled_pages(blocks, numbers), None


def _signal(page: pdf_inspector.PageMarkdown) -> PageSignal:
    """One page's planner signal: pdf-inspector's OCR verdict + Markdown volume."""
    text = page.markdown
    has_text = bool(text.strip())
    return PageSignal(
        page_number=page.page + 1,
        has_native_text=has_text and not page.needs_ocr,
        text_chars=len(text),
        image_count=0,
        blank=not has_text,
        replacement_char_ratio=(text.count(_REPLACEMENT_CHAR) / len(text)) if text else None,
    )


def _in_range(page_number: int, page_range: PageRange | None) -> bool:
    """Whether a 1-based page falls in the request (``None`` = the whole document)."""
    return page_range is None or page_range.start <= page_number <= page_range.end


def _filled_pages(blocks: dict[int, list[StructuredChunk]], numbers: list[int]) -> list[PageResult]:
    """Every requested page in order — empty when it produced no chunks."""
    return [PageResult(page_number=number, blocks=blocks.get(number, [])) for number in numbers]


def _failure(request: ConversionRequest, started: float, code: FailureCode, detail: str) -> PassFailure:
    return PassFailure(
        code=code,
        pass_kind=PassKind.NATIVE,
        page_range=request.page_range,
        backend=DESCRIPTOR.name,
        backend_version=PDF_INSPECTOR_BACKEND_VERSION,
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
    """Filesystem path for a ``file://`` URI via the shared resolver."""
    return path_from_file_uri(uri)
