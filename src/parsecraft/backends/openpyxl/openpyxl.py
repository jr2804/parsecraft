"""The openpyxl backend: XLSX → typed ``TABLE`` chunks, one per worksheet.

openpyxl is a **base dependency** (MIT; a pure-Python wheel with one pure-Python
dependency, ``et-xmlfile``), so this backend needs no extra, no licence gate, no
model weights and no GPU.

Why the import is still deferred — and why there is no light/heavy split:
``import openpyxl`` measures **137 ms cold** on the development host (against
0.5 ms for a dependency-free factory module), and registry discovery imports
every entry point's factory module eagerly on the first registry use, so a
module-scope import would add that cost to *every* command — including a
PDF-only conversion that never touches a spreadsheet. The deferral is therefore
still warranted; the split into a light factory module plus a heavy ``_impl`` is
not, because its only remaining purpose was deferral, and :func:`import_module`
at call time does that inside one module. ``importlib`` is also the spelling
``pyreorder`` never hoists, so the call site survives a formatter pass
(POLICIES: imports must be hoist-safe).

Design notes:
- **Typed rows, never parsed Markdown** (root AGENTS.md rule 4). The grid comes
  from ``Worksheet.iter_rows(values_only=True)``; ``content`` is the GitHub-style
  table rendered *from* those rows so the Markdown projection keeps working.
  Nothing reads Markdown back into state.
- **One typed ``TABLE`` chunk per worksheet**, all on page 1: a workbook has no
  pagination, so ``supports_page_ranges``/``supports_multi_page`` stay off and a
  ``page_range`` starting beyond page 1 is a typed ``INVALID_INPUT`` record.
- **The sheet name travels as ``metadata["sheet"]``** — a kind-specific metadata
  hint, not prose and not a ``CAPTION`` chunk, so one-chunk-per-sheet holds.
- **Blanks:** trailing all-blank rows are dropped and trailing blank columns
  removed, so the grid is rectangular without a sheet-sized tail of empties.
  Interior blanks are kept — an empty cell mid-row is data, not padding.
- **Reading is non-destructive:** ``read_only=True, data_only=True`` — no formula
  evaluation, no write-back, a formula cell yields its cached value.
- **Bounds:** cancellation, the range and the deadline are checked before the
  (blocking) load; the output budget is checked after it. Failures are typed
  records, never raises.
- **``analyze()`` is a size proxy by design** (the ``pandoc``/``native``
  precedent): a workbook is zipped XML, so plain text cannot be counted cheaply;
  ``text_chars`` is the byte length, which keeps a real workbook native and never
  escalates a text-bearing format to OCR.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from importlib import import_module
from io import BytesIO
from time import monotonic
from typing import IO, Protocol, cast

from parsecraft.backends.errors import BackendError
from parsecraft.backends.native._common import source_bytes
from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
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

#: Backend version (adapter code, not the openpyxl release).
OPENPYXL_BACKEND_VERSION = "0.1.0"

#: The single verified MIME input (.xlsx; the macro-enabled .xlsm is untested).
OPENPYXL_FORMATS: tuple[str, ...] = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",)

#: The module deferred to call time — see the module docstring for the 137 ms reason.
_OPENPYXL_MODULE = "openpyxl"

#: The single logical page every workbook converts to.
_WORKBOOK_PAGE = 1

CAPABILITIES = BackendCapabilities(
    supported_formats=list(OPENPYXL_FORMATS),
    # A workbook is one logical page: no layout, no pagination.
    supports_page_ranges=False,
    supports_multi_page=False,
    estimated_vram_gb=None,
    optional_dependency_group=None,  # base dependency: no group to install
    convert_extra=None,  # ...so conversion crosses no extra boundary either
    model_asset=None,  # a parser library — no model weights to pin
)

DESCRIPTOR = BackendDescriptor(
    name="openpyxl",
    version=OPENPYXL_BACKEND_VERSION,
    capabilities=CAPABILITIES,
)


class _Sheet(Protocol):
    """The slice of a worksheet this backend reads."""

    title: str

    def iter_rows(self, *, values_only: bool) -> Iterator[tuple[object, ...]]:
        """Row-major cell values, empty cells as ``None``."""
        ...


class _Workbook(Protocol):
    """The slice of a workbook this backend reads."""

    worksheets: list[_Sheet]

    def close(self) -> None:
        """Release the workbook's file handles."""
        ...


class _OpenpyxlModule(Protocol):
    """The slice of the openpyxl module this backend uses."""

    def load_workbook(self, filename: IO[bytes], *, read_only: bool, data_only: bool) -> _Workbook:
        """Open a workbook from a binary stream."""
        ...


class OpenpyxlBackend:
    """Instantiated openpyxl backend: cheap analyze + single-page conversion."""

    name = DESCRIPTOR.name
    capabilities = DESCRIPTOR.capabilities

    def __init__(self, config: BackendConfig) -> None:
        self._config = config
        # Resolved once, here, so a broken install fails at registry.create with
        # a typed error instead of mid-convert.
        self._openpyxl = _openpyxl()

    def convert(self, request: ConversionRequest) -> BackendResult:
        """Convert every worksheet into one typed ``TABLE`` chunk (failures are records)."""
        started = monotonic()
        reference = BackendRef(name=self.name, version=OPENPYXL_BACKEND_VERSION)
        if request.cancellation is not None and request.cancellation():
            failure = _failure(request, started, FailureCode.CANCELLED, "cancelled before extraction", None)
            return _result(reference, [], [failure], started)
        data = source_bytes(request.source)
        media_type = request.source.media_type or ""
        if media_type not in OPENPYXL_FORMATS:
            failure = _failure(
                request,
                started,
                FailureCode.INVALID_INPUT,
                f"openpyxl reads {OPENPYXL_FORMATS[0]!r}, not {media_type!r}",
                None,
            )
            return _result(reference, [], [failure], started)
        failure = _window_failure(request, started)
        if failure is not None:
            return _result(reference, [], [failure], started)
        try:
            workbook = self._openpyxl.load_workbook(BytesIO(data), read_only=True, data_only=True)
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


class OpenpyxlFactory:
    """Light factory: the entry point resolves to this, never to openpyxl."""

    descriptor: BackendDescriptor = DESCRIPTOR

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        return OpenpyxlBackend(config)


def _openpyxl() -> _OpenpyxlModule:
    """The openpyxl module, imported at call time — see the module docstring.

    A missing openpyxl is a broken install, not a missing optional extra: the
    dependency is unconditional, so the error says so instead of naming an extra
    that no longer exists.
    """
    try:
        return cast(_OpenpyxlModule, import_module(_OPENPYXL_MODULE))
    except ImportError as exc:
        msg = f"openpyxl is a base dependency but could not be imported ({exc}); the install is broken"
        raise BackendError(msg) from exc


def _sheet_chunks(workbook: _Workbook) -> list[StructuredChunk]:
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


def _grid(sheet: _Sheet) -> tuple[tuple[str, ...], ...]:
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


factory = OpenpyxlFactory()
