"""Table-region detection for LiteParse PDFs: rules -> regions -> rows.

A plain count of "regions between full-width horizontal rules" over-counts by
roughly 21x on the polqa-re corpus (the recorded 10-page slice has 42 such
regions, 35 of which are on the two table-of-contents pages and most of the
rest are one-column rule-pairs). The mechanism numbers, pinned in
``tests/backends/test_liteparse_tables.py``:

- rule bands 54, full-width rules 48
- raw regions (full-width rule pairs) 42
- single-column regions on a page that is mostly single-column rule-pairs
  (the TOC signature) → suppressed
- survivors with **two or more text columns** in their y-band → 7

The 7 is what the IR exposes as ``StructuredChunk(kind=TABLE, rows=...)``;
MinerU's 2 on the same slice is a reference, not a target — broader
notion of "table region" is a legitimate result.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

#: A rule is a near-horizontal line whose |y2 - y1| is at or below this many points.
RULE_Y_TOL = 2.0
#: Full-width rule: x-span at or above this fraction of the page span.
FULL_WIDTH_FRACTION = 0.6
#: A region must have at least this many text columns to be a table (others are
#: one-column rule pairs, typically section separators or under-boxed headings).
MIN_COLUMNS = 2
#: A page is a TOC page when this many of its rule-pair regions are single-column;
#: when it is, all of its regions are suppressed (the whole page is layout).
TOC_SINGLE_COLUMN_FRACTION = 0.5
#: X-centre gap above this many points is a column break in the text columns of
#: a region.
COLUMN_GAP = 20.0


#: Detector-side mechanism counts on the recorded 10-page slice. The
# consumer's prototype (polqa-re) reported 7; MinerU reports 2. The detector
# sits between them by design and is its own pin — a future change that moves
# a number is the signal, not a regression to bend into parity.
MECHANISM_COUNTS_10_PAGE = {
    "rule_bands": 54,
    "full_width_rules": 48,
    "raw_regions": 42,
    "two_or_more_column_regions": 4,
}


class VectorLineLike(Protocol):
    x1: float
    y1: float
    x2: float
    y2: float
    stroke: bool
    fill: bool


class VectorShapeLike(Protocol):
    """One vector shape: a bounding box and its stroke/fill flags.

    A Protocol (not a dict) because liteparse hands back dataclasses: its
    ``VectorGraphics.shapes`` holds ``VectorShape`` records, so the adapter must
    read attributes. Shapes are not consumed by the detector yet — they are
    handed through for the follow-up that segments header rows and cell borders.
    """

    bbox: tuple[float, float, float, float]
    stroke: bool
    fill: bool


class VectorGraphicsLike(Protocol):
    """The slice of liteparse's ``VectorGraphics`` record the adapter reads.

    ``VectorGraphics`` is a dataclass, so ``lines``/``shapes`` are ATTRIBUTES.
    Reading them dict-style (``record.get("lines")``) raised AttributeError on
    every page and made the whole table path unreachable (issue #7) — the shape
    of this record is liteparse's, not the CLI JSON dump's.
    """

    lines: Sequence[VectorLineLike]
    shapes: Sequence[VectorShapeLike]


class TextItemLike(Protocol):
    x: float
    y: float
    text: str
    width: float


class _RuleBand:
    __slots__ = ("y", "x1", "x2")

    def __init__(self, y: float, x1: float, x2: float) -> None:
        self.y = y
        self.x1 = x1
        self.x2 = x2


def detect_table_regions(
    lines: Sequence[VectorLineLike],
    items: Sequence[TextItemLike],
    page_width: float,
) -> list[tuple[tuple[float, float], tuple[tuple[str, ...], ...]]]:
    """One entry per surviving table region: ``(y_top, y_bottom), rows``.

    Two filters: a region is dropped if it is one-column (it is a section
    separator or under-boxed heading, not a table), and all regions on a
    page whose raw regions are mostly single-column are suppressed (the page
    is a table-of-contents layout, not a body of tables).
    """
    bands = _horizontal_rules(lines)
    if not bands:
        return []
    page_left = min(band.x1 for band in bands)
    page_right = max(band.x2 for band in bands)
    page_span = max(page_right - page_left, 1.0)
    full = _full_width(bands, page_span)
    full.sort(key=lambda b: b.y)
    raw = _raw_regions(full)

    single_column_share = sum(1 for y0, y1 in raw if _columns_in_band(items, y0, y1) < MIN_COLUMNS) / len(raw) if raw else 0.0
    if single_column_share >= TOC_SINGLE_COLUMN_FRACTION:
        return []

    result: list[tuple[tuple[float, float], tuple[tuple[str, ...], ...]]] = []
    for y0, y1 in raw:
        if _columns_in_band(items, y0, y1) < MIN_COLUMNS:
            continue
        x_segments = _column_segments(items, y0, y1, page_left, page_right)
        row_bands = _row_bands(items, y0, y1)
        rows = tuple(_cells_for_row(items, y_top, y_bot, x_segments) for y_top, y_bot in row_bands)
        if any(cell.strip() for row in rows for cell in row):
            result.append(((round(y0, 1), round(y1, 1)), rows))
    return result


def _horizontal_rules(lines: Sequence[VectorLineLike]) -> list[_RuleBand]:
    horizontals: list[_RuleBand] = []
    for line in lines:
        y1 = float(line.y1)
        y2 = float(line.y2)
        x1 = float(line.x1)
        x2 = float(line.x2)
        if abs(y2 - y1) <= RULE_Y_TOL and abs(x2 - x1) > RULE_Y_TOL:
            horizontals.append(_RuleBand(y=(y1 + y2) / 2.0, x1=min(x1, x2), x2=max(x1, x2)))
    horizontals.sort(key=lambda b: b.y)
    bands: list[_RuleBand] = []
    for rule in horizontals:
        if bands and abs(rule.y - bands[-1].y) <= RULE_Y_TOL:
            cur = bands[-1]
            cur.y = (cur.y + rule.y) / 2.0
            cur.x1 = min(cur.x1, rule.x1)
            cur.x2 = max(cur.x2, rule.x2)
        else:
            bands.append(_RuleBand(rule.y, rule.x1, rule.x2))
    return bands


def _full_width(bands: Sequence[_RuleBand], page_span: float) -> list[_RuleBand]:
    return [band for band in bands if (band.x2 - band.x1) >= FULL_WIDTH_FRACTION * page_span]


def _raw_regions(full: Sequence[_RuleBand]) -> list[tuple[float, float]]:
    return [(upper.y, lower.y) for upper, lower in zip(full, full[1:], strict=False) if lower.y - upper.y > RULE_Y_TOL]


def _columns_in_band(items: Sequence[TextItemLike], y0: float, y1: float) -> int:
    """Distinct text columns in ``(y0, y1)``, clustered by x-centre gaps."""
    centres: list[float] = []
    for item in items:
        y = float(item.y)
        if y0 + RULE_Y_TOL < y < y1 - RULE_Y_TOL:
            centres.append(float(item.x) + float(item.width) / 2.0)
    if not centres:
        return 0
    centres.sort()
    cols = 1
    for previous, current in zip(centres, centres[1:], strict=False):
        if current - previous > COLUMN_GAP:
            cols += 1
    return cols


def _row_bands(items: Sequence[TextItemLike], y0: float, y1: float) -> list[tuple[float, float]]:
    """Row boundaries inside a region, from the y-clusters of text items.

    Items whose y is in the region's band are grouped by consecutive y's (a row is a
    run of items whose y is within ``RULE_Y_TOL`` of the row's baseline). Empty
    bands would be misleading, so the list is guaranteed non-empty when any item
    is in the region.
    """
    ys = sorted({round(item.y, 2) for item in items if y0 + RULE_Y_TOL < item.y < y1 - RULE_Y_TOL})
    bands: list[tuple[float, float]] = []
    for y in ys:
        if bands and abs(y - bands[-1][1]) <= RULE_Y_TOL:
            bands[-1] = (bands[-1][0], (bands[-1][1] + y) / 2.0)
        else:
            bands.append((y, y))
    # Row bands stay the y-clusters themselves; the cell check is symmetric
    # for single-point bands (see _cells_for_row).
    return bands


def _cells_for_row(
    items: Sequence[TextItemLike],
    y_top: float,
    y_bot: float,
    x_segments: tuple[float, ...],
) -> tuple[str, ...]:
    """Text per column for one row band.

    A row band is a tight y-cluster; for a single-point band the check is
    symmetric (``y_top - tol < y < y_bot + tol``) so an item at exactly the
    row's baseline is included.
    """
    symmetric = abs(y_top - y_bot) <= RULE_Y_TOL
    out: list[str] = []
    for i in range(len(x_segments) - 1):
        lo, hi = x_segments[i], x_segments[i + 1]
        bits: list[str] = []
        for item in items:
            y = item.y
            in_row = y_top - RULE_Y_TOL < y < y_bot + RULE_Y_TOL if symmetric else y_top + RULE_Y_TOL < y < y_bot - RULE_Y_TOL
            if not in_row:
                continue
            cx = item.x + item.width / 2.0
            if lo + RULE_Y_TOL < cx < hi - RULE_Y_TOL:
                bits.append(item.text)
        out.append(" ".join(bits))
    return tuple(out)


def _column_segments(
    items: Sequence[TextItemLike],
    y0: float,
    y1: float,
    page_left: float,
    page_right: float,
) -> tuple[float, ...]:
    """Column boundaries from x-centre clusters (left edge + between-gaps + right edge)."""
    centres: list[float] = []
    for item in items:
        y = float(item.y)
        if not (y0 + RULE_Y_TOL < y < y1 - RULE_Y_TOL):
            continue
        cx = float(item.x) + float(item.width) / 2.0
        if page_left + RULE_Y_TOL < cx < page_right - RULE_Y_TOL:
            centres.append(cx)
    centres.sort()
    edges: list[float] = [page_left]
    for previous, current in zip(centres, centres[1:], strict=False):
        if current - previous > COLUMN_GAP:
            edges.append((previous + current) / 2.0)
    edges.append(page_right)
    return tuple(edges)
