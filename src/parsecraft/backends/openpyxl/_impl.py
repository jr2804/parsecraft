"""Heavy openpyxl implementation — ``openpyxl`` imported at top level.

Loaded only from the light factory at instantiation (``importlib``-based, never
an inline import). Licence: MIT (``et_xmlfile`` is its sole dependency), so this
extra carries no copyleft gate.

Why XLSX is not pymupdf's job: pymupdf 1.28.2 reads only NUMERIC cells from a
worksheet — string cells disappear from ``text``/``words``/``blocks``/
``drawings``/``images``, verified against both sharedStrings and inlineStr
workbooks — so the cell grid must come from a real spreadsheet reader.

Design notes:
- **Typed rows, never parsed Markdown** (root rule 4). The grid comes from
  ``Worksheet.iter_rows(values_only=True)``; ``content`` is the GitHub-style
  table rendered *from* those rows so the Markdown projection keeps working.
  Nothing reads Markdown back into state.
- **One typed ``TABLE`` chunk per worksheet**, all on page 1: a workbook has no
  pagination, so ``supports_page_ranges``/``supports_multi_page`` stay off and a
  ``page_range`` starting beyond page 1 is a typed ``INVALID_INPUT`` record.
- **The sheet name travels as ``metadata["sheet"]``** — structured metadata, not
  prose and not a second chunk, so the one-chunk-per-sheet invariant holds.
- **Blanks:** trailing all-blank rows are dropped and trailing blank columns
  removed, so the grid is rectangular without a sheet-sized tail of empties.
  Interior blanks are kept — an empty cell mid-row is data, not padding.
- **Reading is non-destructive:** ``read_only=True, data_only=True`` — no formula
  evaluation, no write-back, a formula cell yields its cached value.
- **Bounds:** cancellation, the range and the deadline are checked before the
  (blocking) load; the output budget is checked after it. Failures are typed
  records, never raises.
- **``analyze()`` is a size proxy by design** (the ``pandoc``/``native``
  precedent): a workbook is zipped XML, so plain text cannot be counted
  cheaply; ``text_chars`` is the byte length, which keeps a real workbook
  native and never escalates a text-bearing format to OCR.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from time import monotonic
from typing import Any

import openpyxl  # ty: ignore[unresolved-import] — optional: "openpyxl" extra

from parsecraft.backends.native._common import source_bytes
from parsecraft.backends.openpyxl.openpyxl import DESCRIPTOR, OPENPYXL_BACKEND_VERSION
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

#: The one MIME this implementation reads; mirrors the descriptor's list.
_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: The single logical page every workbook converts to.
_WORKBOOK_PAGE = 1


class OpenpyxlBackend:
    """Instantiated openpyxl backend: cheap analyze + single-page conversion."""

    name = DESCRIPTOR.name
    capabilities = DESCRIPTOR.capabilities

    def __init__(self, config: BackendConfig) -> None:
        self._config = config

    def convert(self, request: ConversionRequest) -> BackendResult:
        """Convert every worksheet into one typed ``TABLE`` chunk (failures are records)."""
        started = monotonic()
        reference = BackendRef(name=self.name, version=OPENPYXL_BACKEND_VERSION)
        if request.cancellation is not None and request.cancellation():
            failure = _failure(request, started, FailureCode.CANCELLED, "cancelled before extraction", None)
            return _result(reference, [], [failure], started)
        data = source_bytes(request.source)
        media_type = request.source.media_type or ""
        if media_type != _XLSX_MEDIA_TYPE:
            failure = _failure(
                request,
                started,
                FailureCode.INVALID_INPUT,
                f"openpyxl reads {_XLSX_MEDIA_TYPE!r}, not {media_type!r}",
                None,
            )
            return _result(reference, [], [failure], started)
        failure = _window_failure(request, started)
        if failure is not None:
            return _result(reference, [], [failure], started)
        try:
            workbook = openpyxl.load_workbook(BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001 — openpyxl raises a wide family (zip, XML, cell) with no common base
            failure = _failure(
                request,
                started,
                FailureCode.BACKEND_ERROR,
                f"openpyxl cannot read {request.source.uri!r}: {type(exc).__name__}: {exc}",
                PageRange(start=_WORKBOOK_PAGE, end=_WORKBOOK_PAGE),
            )
            return _result(reference, [], [failure], started)
        try:
            blocks = _sheet_chunks(workbook)
        finally:
            workbook.close()
        produced = sum(len(chunk.content) for chunk in blocks)
        if request.max_output_chars is not None and produced > request.max_output_chars:
            failure = _failure(
                request,
                started,
                FailureCode.BUDGET_EXCEEDED,
                f"output budget of {request.max_output_chars} chars exceeded ({produced} produced)",
                PageRange(start=_WORKBOOK_PAGE, end=_WORKBOOK_PAGE),
            )
            return _result(reference, [], [failure], started)
        return _result(reference, [PageResult(page_number=_WORKBOOK_PAGE, blocks=blocks)], [], started)

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        """Cheap size-proxy pass: one page, text-bearing format, no workbook load."""
        data = source_bytes(source)
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


def create(config: BackendConfig) -> DocumentBackend:
    """Provider entry for the light factory — the sanctioned heavy boundary."""
    return OpenpyxlBackend(config)


def _sheet_chunks(workbook: Any) -> list[StructuredChunk]:
    """One typed ``TABLE`` chunk per worksheet, in workbook order."""
    chunks: list[StructuredChunk] = []
    for index, sheet in enumerate(workbook.worksheets):
        rows = _grid(sheet)
        chunks.append(
            StructuredChunk(
                id=f"openpyxl-p{_WORKBOOK_PAGE}-b{index}",
                kind=ChunkKind.TABLE,
                content=_markdown_table(rows),
                page_number=_WORKBOOK_PAGE,
                reading_order=index,
                rows=rows,
                metadata={"backend": "openpyxl", "sheet": sheet.title},
            )
        )
    return chunks


def _grid(sheet: Any) -> tuple[tuple[str, ...], ...]:
    """The sheet's cells as a rectangular grid of text, trailing blanks removed.

    Empty cells are ``""``: a cell the sheet never filled and a cell holding an
    empty string are the same fact for a consumer. Interior blanks stay —
    dropping them would shift columns. ``None`` becomes ``""`` because a
    spreadsheet's "no value" is sparse data, not the string "None".
    """
    raw = [tuple("" if cell is None else str(cell) for cell in row) for row in sheet.iter_rows(values_only=True)]
    while raw and not any(cell.strip() for cell in raw[-1]):
        raw.pop()
    width = 0
    for row in raw:
        trimmed = len(row)
        while trimmed > 0 and not row[trimmed - 1].strip():
            trimmed -= 1
        width = max(width, trimmed)
    if width == 0:
        return ()
    return tuple(tuple(row[:width]) + ("",) * (width - min(len(row), width)) for row in raw)


def _markdown_table(rows: tuple[tuple[str, ...], ...]) -> str:
    """Render the grid as GitHub-style Markdown — the projection of the rows.

    Same shape the Markdown adapter produces, so a workbook chunk round-trips
    through the projection exactly like a table that came from Markdown.
    """
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(rows[0]) + " |",
        "| " + " | ".join(["---"] * len(rows[0])) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(lines)


def _window_failure(request: ConversionRequest, started: float) -> PassFailure | None:
    """Range and deadline checks before the blocking load (None = proceed)."""
    if request.page_range is not None and request.page_range.start > _WORKBOOK_PAGE:
        return _failure(
            request,
            started,
            FailureCode.INVALID_INPUT,
            "requested page range is outside the document (a workbook is a single page)",
            request.page_range,
        )
    deadline = started + request.timeout_s if request.timeout_s is not None else None
    if deadline is not None and monotonic() >= deadline:
        return _failure(
            request,
            started,
            FailureCode.TIMEOUT,
            "time budget exhausted before conversion",
            PageRange(start=_WORKBOOK_PAGE, end=_WORKBOOK_PAGE),
        )
    return None


def _failure(
    request: ConversionRequest,
    started: float,
    code: FailureCode,
    detail: str,
    page_range: PageRange | None,
) -> PassFailure:
    """One typed failure record (ADR-0001 §7) for the processing trace."""
    return PassFailure(
        code=code,
        pass_kind=PassKind.NATIVE,
        page_range=page_range,
        backend="openpyxl",
        backend_version=OPENPYXL_BACKEND_VERSION,
        budget_s=request.timeout_s if request.timeout_s is not None else 0.0,
        elapsed_s=max(monotonic() - started, 0.0),
        detail=detail,
        occurred_at=utcnow(),
    )


def _result(
    reference: BackendRef,
    pages: list[PageResult],
    failures: list[PassFailure],
    started: float,
) -> BackendResult:
    return BackendResult(
        backend=reference,
        pages=pages,
        failures=failures,
        elapsed_s=max(monotonic() - started, 0.0),
    )


__all__ = ["OpenpyxlBackend", "create"]
