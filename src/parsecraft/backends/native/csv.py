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
from collections.abc import Sequence

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
#: Head lines the sniff reads: a header plus a few data rows establish
#: consistency; the bound keeps sniffing cheap on wide files.
_SNIFF_LINES = 5
#: Delimiter candidates in preference order — the tie-break when two are
#: equally consistent (RFC4180 comma first, then the common variants).
_SNIFF_CANDIDATES = (",", ";", "\t", "|")
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
        delimiter = _sniff_delimiter(_head_lines(text))
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


def _head_lines(text: str) -> list[str]:
    """First non-blank physical lines for the sniff (bounded, deterministic).

    Blank lines are filtered deliberately: they carry no delimiter signal, so
    the sniff sample and the parse text intentionally disagree here.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[:_SNIFF_LINES]


def _sniff_delimiter(lines: Sequence[str]) -> str | None:
    """Delimiter sniff: the candidate splitting the most head lines, then widest.

    A ragged row must not veto detection (ragged files are in scope), so each
    candidate scores ``(lines split, max width)`` and the best wins; iteration
    order breaks ties, keeping comma's RFC4180 precedence. Returns ``None``
    when no candidate splits anything (single-column input) — the caller falls
    back to comma rather than raising on a file that merely ends in ``.csv``.
    """
    best: str | None = None
    best_score = (0, 0)
    for candidate in _SNIFF_CANDIDATES:
        widths = [_field_count(line, candidate) for line in lines]
        multi = sum(1 for width in widths if width > 1)
        if multi == 0:
            continue
        score = (multi, max(widths))
        if score > best_score:
            best = candidate
            best_score = score
    return best


def _field_count(line: str, delimiter: str) -> int:
    """Field count of one physical line under ``delimiter`` (quote-aware).

    Strict probes: a line broken by a multiline cell raises, and scores 1
    ("splits nothing") instead of propagating — one bad line never vetoes an
    otherwise splitting candidate. The full parse stays lenient by design.
    """
    try:
        return len(next(csv.reader([line], delimiter=delimiter, quotechar='"', doublequote=True, strict=True)))
    except csv.Error:
        return 1


def _parse_rows(text: str, delimiter: str) -> tuple[tuple[str, ...], ...]:
    """Whole-text parse (quote-aware: embedded newlines survive), ragged kept.

    Trailing all-blank rows are file-tail newlines, not data; interior blanks
    are kept verbatim — no sanitization. Empty input yields no rows, and the
    caller emits no chunk for an empty grid (see :func:`extract_csv_pages`).
    """
    parsed = tuple(tuple(row) for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, quotechar='"', doublequote=True))
    end = len(parsed)
    while end > 0 and all(not cell.strip() for cell in parsed[end - 1]):
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
