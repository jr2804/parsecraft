"""Delimited-data native backend — stdlib ``csv``, no external dependencies.

One ``TABLE`` chunk per file: the parsed cell grid as ``rows`` with the
header as row 0 (never lifted into metadata — the consumer keys data rows by
column name, pc-oir.1/kompend-1), Markdown content rendered from the same
grid. ``.tsv`` forces tab; ``.csv`` sniffs the delimiter by RFC4180
consistency over the first lines, then parses with stdlib ``csv`` so quoted
fields, embedded newlines, and CRLF survive. A file that merely ends in
``.csv`` but sniffs to nothing falls back to comma plus an INFO diagnostic —
it degrades, never blocks. An empty grid emits no chunk (a content-less TABLE
would surface downstream as an empty table element) with an INFO diagnostic.
"""

from __future__ import annotations

import csv
import io
import sys

from parsecraft.backends.native._common import NATIVE_BACKEND_VERSION, NativeBackendBase
from parsecraft.backends.protocol import (
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    BackendFactory,
    DocumentBackend,
    SourceDocument,
)
from parsecraft.ir.models import (
    ChunkKind,
    Diagnostic,
    DiagnosticLevel,
    PageResult,
    StructuredChunk,
)

BACKEND_NAME = "native-csv"
SUPPORTED_FORMATS = ["text/csv", "text/tab-separated-values"]

_TSV_MEDIA_TYPE = "text/tab-separated-values"
#: Delimiter candidates in preference order — the tie-break when two are
#: equally consistent (RFC4180 comma first, then the common variants).
_SNIFF_CANDIDATES = (",", ";", "\t", "|")
_SNIFF_RECORDS = 5
_DEFAULT_DELIMITER = ","

#: The stdlib CSV field cap (131072) rejects real-world wide cells (a 200 KB
#: description/JSON/base64 column raises ``csv.Error``); raise it once here so
#: extraction degrades (trims nothing) instead of blocking. Single expression
#: — no branch — so the 100% gate holds on every word size.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


class CsvBackend(NativeBackendBase):
    """Delimited data: one TABLE chunk carrying the parsed cell grid."""

    name = BACKEND_NAME


class _CsvFactory:
    """Light entry-point object: descriptor + build."""

    descriptor = BackendDescriptor(
        name=BACKEND_NAME,
        version=NATIVE_BACKEND_VERSION,
        capabilities=BackendCapabilities(
            supported_formats=SUPPORTED_FORMATS,
            supports_page_ranges=False,
            supports_multi_page=False,
        ),
    )

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        return CsvBackend(config, self.descriptor.capabilities, extract=extract_csv_pages)


def extract_csv_pages(source: SourceDocument, data: bytes) -> list[PageResult]:
    """Parse delimited bytes into one page carrying a single TABLE chunk."""
    text = _decode(data)
    if _is_tsv(source):
        delimiter = "\t"
        diagnostics: list[Diagnostic] = []
    else:
        delimiter = _sniff_delimiter(text)
        diagnostics = []
        if delimiter is None:
            delimiter = _DEFAULT_DELIMITER
            diagnostics.append(
                Diagnostic(
                    level=DiagnosticLevel.INFO,
                    code="csv-sniff-fallback",
                    message="no consistent delimiter in the first lines; parsed as comma-separated",
                )
            )
    rows = _parse_rows(text, delimiter)
    if not rows:
        diagnostics.append(
            Diagnostic(
                level=DiagnosticLevel.INFO,
                code="csv-empty-table",
                message="no data rows; nothing to emit",
            )
        )
        return [PageResult(page_number=1, blocks=[], diagnostics=diagnostics)]
    return [
        PageResult(
            page_number=1,
            blocks=[
                StructuredChunk(
                    id=f"{BACKEND_NAME}-1-b0",
                    kind=ChunkKind.TABLE,
                    content=_markdown_table(rows),
                    page_number=1,
                    reading_order=0,
                    metadata={"backend": BACKEND_NAME, "delimiter": delimiter},
                    rows=rows,
                )
            ],
            diagnostics=diagnostics,
        )
    ]


def _decode(data: bytes) -> str:
    """BOM-aware decode: UTF-16 Excel exports, else UTF-8 with BOM stripped.

    The consumer UTF-8-normalizes ``.csv`` but NOT ``.tsv``, so a UTF-16
    ``.tsv`` reaches the backend undetected — without this branch its NUL-
    interleaved bytes parse as silent junk rows. ``utf-16`` consumes the BOM
    to pick the endianness; ``utf-8-sig`` strips a UTF-8 BOM.
    """
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


def _is_tsv(source: SourceDocument) -> bool:
    """Tab is forced by the declared media type; the suffix is the fallback.

    A declared media type is authoritative (the MEDIA_TYPES join is exact),
    so ``text/csv`` on a ``.tsv``-named source still sniffs for commas.
    """
    if source.media_type is not None:
        return source.media_type == _TSV_MEDIA_TYPE
    return source.uri.lower().endswith(".tsv")


def _sniff_delimiter(text: str) -> str | None:
    """Score candidate dialects; unquoted quote characters weaken a guess.

    Each candidate uses its own logical records. Strict CSV syntax and fewer
    literal quotes outside fields rank ahead of row consistency, which avoids
    mistaking lines inside another candidate's quoted field for extra rows.
    Dialect detection remains a heuristic; ties prefer the candidate order.
    """
    best: str | None = None
    best_score: tuple[int, ...] | None = None
    for candidate in _SNIFF_CANDIDATES:
        try:
            widths, stray_quotes = _probe_delimiter(text, candidate, strict=True)
            strict = 1
        except csv.Error:
            try:
                widths, stray_quotes = _probe_delimiter(text, candidate, strict=False)
            except csv.Error:
                # A competing delimiter can exceed the process field limit
                # even when the real delimiter's fields are all within it.
                continue
            strict = 0
        multi = sum(1 for width in widths if width > 1)
        if multi == 0:
            continue
        single = len(widths) - multi
        score = (strict, -stray_quotes, multi - single, -single, multi, max(widths))
        if best_score is None or score > best_score:
            best = candidate
            best_score = score
    return best


def _probe_delimiter(text: str, delimiter: str, *, strict: bool) -> tuple[list[int], int]:
    """Read at most five nonblank logical records with one CSV dialect."""
    stream = io.StringIO(text, newline="")
    reader = csv.reader(stream, delimiter=delimiter, strict=strict)
    widths: list[int] = []
    stray_quotes = 0
    start = 0
    for row in reader:
        end = stream.tell()
        if row and (len(row) > 1 or any(cell.strip() for cell in row)):
            widths.append(len(row))
            stray_quotes += _stray_quotes(text, start, end, delimiter)
            if len(widths) == _SNIFF_RECORDS:
                break
        start = end
    return widths, stray_quotes


def _stray_quotes(text: str, start: int, end: int, delimiter: str) -> int:
    """Count literal quotes outside quoted fields for this delimiter."""
    quoted = False
    count = 0
    index = start
    while (index := text.find('"', index, end)) != -1:
        if quoted:
            if index + 1 < end and text[index + 1] == '"':
                index += 2
                continue
            quoted = False
        elif index == start or text[index - 1] == delimiter:
            quoted = True
        else:
            count += 1
        index += 1
    return count


def _parse_rows(text: str, delimiter: str) -> tuple[tuple[str, ...], ...]:
    """Whole-text parse (quote-aware: embedded newlines survive), ragged kept.

    Trailing empty physical lines are trimmed; explicit empty or whitespace
    cells and interior blanks are kept verbatim. Empty input yields no rows;
    the caller emits no chunk for an empty grid (see :func:`extract_csv_pages`).
    """
    parsed = tuple(tuple(row) for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, quotechar='"', doublequote=True))
    end = len(parsed)
    while end > 0 and not parsed[end - 1]:
        end -= 1
    return parsed[:end]


def _markdown_table(rows: tuple[tuple[str, ...], ...]) -> str:
    """Markdown projection of the grid (header row 0, separator, data rows).

    Total over ``rows`` (empty included): an empty grid renders empty, so the
    projection never raises on degenerate input.
    """
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [list(row) + [""] * (width - len(row)) for row in rows]
    lines = [f"| {' | '.join(padded[0])} |", f"| {' | '.join(['---'] * width)} |"]
    lines.extend(f"| {' | '.join(row)} |" for row in padded[1:])
    return "\n".join(lines)


factory: BackendFactory = _CsvFactory()
