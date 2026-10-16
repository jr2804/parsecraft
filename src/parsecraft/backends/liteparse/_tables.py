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
- survivors with **two or more text columns** in their y-band → 4, and those
  survivors are MERGED along a rule run so one table is one chunk (pc-4u7.42):
  a row separator is a rule too, and pairing every consecutive pair of rules made
  each ROW its own region — the consumer measured 30 chunks for one 30-row table
  and 98 single-row fragments out of 135 regions on the full 80-page document.

The 4 is what the IR exposes as ``StructuredChunk(kind=TABLE, rows=...)``;
MinerU's 2 on the same slice is a reference, not a target — a broader notion of
"table region" is a legitimate result.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

#: A data-value first cell: a number, optionally signed, with a decimal comma or
#: point, optionally percent, anchored at the start. Trailing text after the
#: number still names a data row (a unit, a continuation of the label).
_NUMERIC_FIRST_CELL = re.compile(r"[-+]?\d+(?:[.,]\d+)?%?")

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
#: Consecutive rule pairs belong to the SAME table when their column boundaries
#: agree within this many points. A row separator is a rule too, so pairing every
#: consecutive pair of rules made each row its own region (the consumer measured
#: 30 chunks for one 30-row table, 98 of 135 regions being single-row fragments).
COLUMN_MATCH_TOL = 4.0


#: Detector-side mechanism counts on the recorded 10-page slice. The
# consumer's prototype (polqa-re) reported 7; MinerU reports 2. The detector
# sits between them by design and is its own pin — a future change that moves
# a number is the signal, not a regression to bend into parity.
MECHANISM_COUNTS_10_PAGE = {
    "rule_bands": 54,
    "full_width_rules": 48,
    "raw_regions": 42,
    "merged_regions": 4,
    "two_or_more_column_regions": 4,
}


#: Bold is font_weight at or above this value (mirrors _items, which owns the
#: heading classification; duplicated here so the partner test stands alone).
BOLD_WEIGHT = 600
#: A bold band taller than this is a section gap, not a shaded row: the partner
#: rule (which would rejoin it to the table above) applies to row-height bands
#: only (measured: shaded/header rows are 12-22 pt, section gaps 100+ pt).
PARTNER_BAND_MAX_HEIGHT = 40.0
#: A bold item wider than this is a wrapped label spanning columns: its centre
#: can sit on a boundary, so it is exempt from the placement test.
WRAPPED_LABEL_WIDTH = 2.0 * COLUMN_GAP


class VectorLineLike(Protocol):
    x1: float
    y1: float
    x2: float
    y2: float
    stroke: bool
    fill: bool


class VectorShapeLike(Protocol):
    """One vector shape: a bounding box, stroke/fill flags, and curve flag.

    A Protocol (not a dict) because liteparse hands back dataclasses: its
    ``VectorGraphics.shapes`` holds ``VectorShape`` records, so the adapter must
    read attributes. ``has_curve`` is the figure signal: the recorder emits flow
    arrows and diagram strokes as CURVED shapes (measured: figure pages carry 1-12
    curved shapes, table pages zero), which is how the detector excludes figure
    geometry from table detection.
    """

    bbox: tuple[float, float, float, float]
    stroke: bool
    fill: bool
    has_curve: bool


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
    separators: Sequence[float] | None = None,
    shapes: Sequence[VectorShapeLike] = (),
) -> list[tuple[tuple[float, float], tuple[tuple[str, ...], ...]]]:
    """One entry per surviving table region: ``(y_top, y_bottom), rows``.

    Two filters: a region is dropped if it is one-column (it is a section
    separator or under-boxed heading, not a table), and all regions on a
    page whose raw regions are mostly single-column are suppressed (the page
    is a table-of-contents layout, not a body of tables). Regions are then
    MERGED along a rule run so one table is one chunk (pc-4u7.42).
    """
    if any(shape.has_curve for shape in shapes):
        # A page with CURVED vector shapes is figure content (measured: flowcharts
        # and timing diagrams carry 1-12 curved shapes — arrows, brackets — while
        # table pages carry none). Its rule bands are the figure's boxes, not
        # tables, so the page yields no regions at all (pc-4u7.42 round 4: pages
        # 31/35 are Figures 5/6, and the right count there is 0).
        return []
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
    for y0, y1, x_segments in _rule_runs(full, items, page_left, page_right, separators or ()):
        row_bands = _row_bands(items, y0, y1)
        rows = tuple(_cells_for_row(items, y_top, y_bot, x_segments) for y_top, y_bot in row_bands)
        if any(cell.strip() for row in rows for cell in row):
            result.append(((round(y0, 1), round(y1, 1)), rows))
    return result


def _rule_runs(
    full: Sequence[_RuleBand],
    items: Sequence[TextItemLike],
    page_left: float,
    page_right: float,
    separators: Sequence[float],
) -> list[tuple[float, float, tuple[float, ...]]]:
    """One entry per table: ``(y_top, y_bottom, column boundaries)``.

    Three things close a run, in the order the consumer's evidence established
    them (pc-4u7.42):

    - a rule pair holding no table row (a one-column band, a section separator):
      the region ends at the last rule before a non-tabular gap;
    - a rule pair whose band holds a HEADING-classified line (``separators``,
      the y of every bold non-TOC line on the page — a table's own bold header
      row, or a section heading between tables): structure, not geometry;
    - a rule pair whose columns do not match the run's accumulated model.

    The model is count-agnostic and self-refining: a wrapped cell can add or lose
    a detected column (the consumer measured a row at 5 columns followed by 6),
    so a pair belongs when its outer boundaries match and a MAJORITY of the
    interior boundaries match in both directions — a genuinely different table
    moves most of them. Each accepted pair then nudges the model toward itself
    (nearest-match averaging), so drift moves the model instead of ending the run.
    """
    runs: list[tuple[float, float, tuple[float, ...]]] = []
    model: tuple[float, ...] | None = None
    for upper, lower in zip(full, full[1:], strict=False):
        if lower.y - upper.y <= RULE_Y_TOL or _columns_in_band(items, upper.y, lower.y) < MIN_COLUMNS:
            # No table row in this pair: close the run. Skipping without closing
            # would let one run span a section break just because the columns on
            # either side happen to line up.
            model = None
            continue
        segments = _column_segments(items, upper.y, lower.y, page_left, page_right)
        has_separator = any(upper.y < y < lower.y for y in separators)
        if model is not None and (
            _same_columns(model, segments)
            or _continues_numbered_run(items, upper.y, lower.y)
            or (has_separator and lower.y - upper.y <= PARTNER_BAND_MAX_HEIGHT and _bold_row_has_column_partners(model, items, upper.y, lower.y))
        ):
            # Membership first: a bold row whose items sit inside the model's
            # columns is a shaded row of THIS table (page 2's highlighted
            # P.800 row), so boldness alone must not close the run — only a bold
            # line WITHOUT column partners is a heading (pc-4u7.42 round 4).
            model = _absorb(model, segments)
            runs[-1] = (runs[-1][0], lower.y, model)
            continue
        if has_separator:
            # Bold and membership failed: a real heading or a caption. The band is
            # furniture — it closes the run and starts no region.
            model = None
            continue
        model = segments
        runs.append((upper.y, lower.y, segments))
    return runs


def _bold_row_has_column_partners(
    model: tuple[float, ...],
    items: Sequence[TextItemLike],
    y0: float,
    y1: float,
) -> bool:
    """Does a bold row sit inside the table's columns (a shaded row, not a heading)?

    The reporter's rendered evidence: page 2's bold row is the P-series box's
    CURRENT-recommendation row, highlighted — same columns as its neighbours —
    while a real heading spans the page or ignores the columns. Partner means
    every bold item's centre falls strictly inside one of the model's columns
    (between two adjacent boundaries), so a wide wrapped label still counts
    (its centre is what places it).
    """
    bold = [
        item
        for item in items
        if y0 + RULE_Y_TOL < item.y < y1 - RULE_Y_TOL and str(item.text).strip() and (getattr(item, "font_weight", 0) or 0) >= BOLD_WEIGHT
    ]
    if not bold:
        return False
    for item in bold:
        if float(item.width) > WRAPPED_LABEL_WIDTH:
            # A wide wrapped label spans several columns; its centre can sit on a
            # boundary, so the small items beside it are what place the row.
            continue
        cx = float(item.x) + float(item.width) / 2.0
        inside = any(left + COLUMN_MATCH_TOL < cx < right - COLUMN_MATCH_TOL for left, right in zip(model, model[1:], strict=False))
        if not inside:
            return False
    return True


def _continues_numbered_run(items: Sequence[TextItemLike], y0: float, y1: float) -> bool:
    """Does this band carry a numbered DATA row of the run above?

    The reporter's continuation rule (pc-4u7.42, option 3): a row whose first
    cell is a data value (``7``, ``6.3``, ``0.0001``) belongs to the table above
    it even when its measured columns drifted — wrapped cells and merged cells
    change the measured count, but a numbered first cell names its row of a
    numbered test-case table. A label first cell (``Frequency (Hz)``) is a new
    table's header, which is exactly the distinction page 19 needs to stay two
    tables. A bold header row never reaches this check: separators close the run
    first.
    """
    first = min(
        (item for item in items if y0 + RULE_Y_TOL < item.y < y1 - RULE_Y_TOL and str(item.text).strip()),
        key=lambda item: float(item.x),
        default=None,
    )
    if first is None:
        return False
    return _NUMERIC_FIRST_CELL.match(str(first.text).strip()) is not None


def _absorb(model: tuple[float, ...], candidate: tuple[float, ...]) -> tuple[float, ...]:
    """Refine the run's model with an accepted pair's edges.

    Each model edge drifts halfway toward the mean of the candidate edges within
    :data:`COLUMN_MATCH_TOL` of it; unmatched edges keep their position (an extra
    candidate edge from a wrapped cell is noise, not structure).
    """
    out: list[float] = []
    for edge in model:
        near = [value for value in candidate if abs(value - edge) <= COLUMN_MATCH_TOL]
        if near:
            out.append((edge + sum(near) / len(near)) / 2.0)
        else:
            out.append(edge)
    return tuple(out)


def _same_columns(reference: tuple[float, ...], candidate: tuple[float, ...]) -> bool:
    """Does a rule pair belong to the run? Count-agnostic mutual-majority match.

    The OUTER boundaries must match (a table's left and right edges do not move),
    and at least half of EACH side's interior boundaries must find a partner
    within :data:`COLUMN_MATCH_TOL` — reference-majority so a sparse row still
    matches a rich model, candidate-majority so a wrapped row's extra column does
    not smuggle in a different structure. A different table changes the count or
    moves most of the interiors, failing one of the two majorities.
    """
    # No outer-edge check: _column_segments pins the first and last edges to the
    # page bounds, so reference[0]/[-1] and candidate[0]/[-1] are the same values
    # by construction and comparing them is vacuous. The mutual interior majority
    # below is the whole decision.
    matched_reference = sum(1 for edge in reference[1:-1] if _edges_align(edge, candidate))
    matched_candidate = sum(1 for edge in candidate[1:-1] if _edges_align(edge, reference))
    reference_interiors = len(reference) - 2
    candidate_interiors = len(candidate) - 2
    return matched_reference * 2 >= reference_interiors and matched_candidate * 2 >= candidate_interiors


def _edges_align(edge: float, edges: tuple[float, ...]) -> bool:
    """Is one boundary within tolerance of some boundary on the other side?"""
    return any(abs(edge - other) <= COLUMN_MATCH_TOL for other in edges)


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
