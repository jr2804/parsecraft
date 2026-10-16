"""Table-region detection: pure `_tables` + acceptance against the recorded fixture."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parsecraft.backends.liteparse._tables import (
    MECHANISM_COUNTS_10_PAGE,
    _bold_row_has_column_partners,
    _columns_in_band,
    _horizontal_rules,
    detect_table_regions,
)

#: Recorded fixtures (liteparse 2.16.0; see README for provenance).
_FIXTURES = Path("tests/backends/fixtures/liteparse")


# ── pure unit tests ───────────────────────────────────────────────────────────


def test_horizontal_rules_group_near_baselines() -> None:
    bands = _horizontal_rules([_line(0, 100, 100, 100), _line(0, 100.5, 100, 100.5), _line(0, 200, 100, 200)])
    assert len(bands) == 2


def test_full_width_is_a_fraction_of_page_span() -> None:
    # 60 % is the threshold.
    wide = [_line(0, 100, 60, 100), _line(0, 100, 100, 100)]  # one of the two is wide
    bands = _horizontal_rules(wide)
    page_span = 100
    full = [b for b in bands if (b.x2 - b.x1) >= 0.6 * page_span]
    assert len(full) == 1
    assert full[0].x2 - full[0].x1 == 100


def test_one_column_regions_are_dropped() -> None:
    # Two full-width rules + a single column of text between them -> 0 tables.
    lines = [_line(0, 100, 200, 100), _line(0, 200, 200, 200)]
    items = [_text(50, 150, "single column"), _text(50, 170, "of text")]
    assert detect_table_regions(lines, items, page_width=200) == []


def test_two_or_more_columns_yields_rows() -> None:
    lines = [_line(0, 100, 200, 100), _line(0, 200, 200, 200)]
    items = [
        _text(20, 130, "left-top"),
        _text(20, 180, "left-bot"),
        _text(120, 130, "right-top"),
        _text(120, 180, "right-bot"),
    ]
    regions = detect_table_regions(lines, items, page_width=200)
    assert len(regions) == 1
    _, rows = regions[0]
    assert len(rows) == 2
    # Two columns (left, right), two rows (top, bot).
    assert rows[0][0] == "left-top"
    assert rows[0][1] == "right-top"
    assert rows[1][0] == "left-bot"
    assert rows[1][1] == "right-bot"


# ── fixture acceptance: the recorded 10-page slice ───────────────────────────


@pytest.mark.skipif(not (_FIXTURES / "items.json").exists() or not (_FIXTURES / "vec.json").exists(), reason="recorded fixtures not present in this checkout")
def test_mechanism_counts_on_the_recorded_10_page_slice() -> None:
    """The mechanism numbers are pinned: rule bands 54, full-width 48, raw 42, ≥2-col 7.

    These are the mechanism numbers, not a MinerU parity target. MinerU reports
    2 table blocks on this slice; the broader notion of "table region" is a
    legitimate result of the detector and is reported as 7, not collapsed to
    2 to match MinerU.
    """
    items_doc = json.loads((_FIXTURES / "items.json").read_text(encoding="utf-8"))
    vec_doc = json.loads((_FIXTURES / "vec.json").read_text(encoding="utf-8"))

    rule_bands = full_width = raw_regions = two_or_more_column = 0
    for index, page in enumerate(items_doc["pages"], start=1):
        vec_page = vec_doc["pages"][index - 1]
        items = [
            SimpleNamespace(
                x=it["x"],
                y=it["y"],
                text=it.get("text", ""),
                width=it.get("width", 0.0),
            )
            for it in (page.get("text_items") or [])
        ]
        lines = [SimpleNamespace(**ln) for ln in (vec_page.get("vector_graphics") or {}).get("lines", [])]
        bands = _horizontal_rules(lines)
        rule_bands += len(bands)
        width = float(page.get("width") or 0.0)
        span = max((max(b.x2 for b in bands) - min(b.x1 for b in bands)) if bands else width, 1.0)
        full = [b for b in bands if (b.x2 - b.x1) >= 0.6 * span]
        full_width += len(full)
        full.sort(key=lambda b: b.y)
        raw = [(upper.y, lower.y) for upper, lower in zip(full, full[1:], strict=False) if lower.y - upper.y > 2.0]
        raw_regions += len(raw)
        single_share = sum(1 for y0, y1 in raw if _columns_in_band(items, y0, y1) < 2) / len(raw) if raw else 0.0
        if single_share >= 0.5:
            continue
        for y0, y1 in raw:
            if _columns_in_band(items, y0, y1) >= 2:
                two_or_more_column += 1

    assert rule_bands == MECHANISM_COUNTS_10_PAGE["rule_bands"]
    assert full_width == MECHANISM_COUNTS_10_PAGE["full_width_rules"]
    assert raw_regions == MECHANISM_COUNTS_10_PAGE["raw_regions"]
    assert two_or_more_column == MECHANISM_COUNTS_10_PAGE["two_or_more_column_regions"]


def _columns_in_band_mirror(items: list[Any], y0: float, y1: float) -> int:
    """Mirror of `_tables._columns_in_band`; kept as a cross-check of the module's own."""
    centres: list[float] = []
    for it in items:
        y = float(it.y)
        if y0 + 2.0 < y < y1 - 2.0:
            centres.append(float(it.x) + float(it.width) / 2.0)
    if not centres:
        return 0
    centres.sort()
    cols = 1
    for a, b in zip(centres, centres[1:], strict=False):
        if b - a > 20.0:
            cols += 1
    return cols


def test_degenerate_inputs_are_tolerated() -> None:
    """No rules, no text, vertical-only lines: every degenerate case returns empty."""
    assert detect_table_regions([], [], page_width=100) == []
    vertical = SimpleNamespace(x1=10.0, y1=0.0, x2=10.0, y2=100.0, stroke=True, fill=False)
    assert _horizontal_rules([vertical]) == []
    assert _columns_in_band([], 0.0, 100.0) == 0


def test_nearby_baselines_merge_into_one_band_and_one_row() -> None:
    lines = [_line(0, 100, 200, 100), _line(0, 200, 200, 200)]
    assert len(_horizontal_rules(lines)) == 2
    near = [_line(0, 100, 200, 100), _line(0, 100.5, 200, 100.5)]
    assert len(_horizontal_rules(near)) == 1

    # Two text baselines within the tolerance are one row, not two.
    items = [_text(20, 130, "a"), _text(120, 130, "b"), _text(20, 131, "c"), _text(120, 131, "d")]
    regions = detect_table_regions(lines, items, page_width=200)
    assert len(regions) == 1
    assert len(regions[0][1]) == 1


def test_a_region_with_no_cell_text_is_not_a_table() -> None:
    """Columns exist by geometry but no cell has text: nothing is emitted."""
    lines = [_line(0, 100, 200, 100), _line(0, 200, 200, 200)]
    items = [_text(20, 130, ""), _text(120, 130, ""), _text(20, 180, ""), _text(120, 180, "")]
    assert detect_table_regions(lines, items, page_width=200) == []


def test_a_single_column_region_among_multi_column_ones_is_skipped() -> None:
    """TOC suppression does not fire (share < 0.5), so the one-column region is skipped."""
    lines = [_line(0, y, 200, y) for y in (100, 200, 300, 400)]
    items = [
        _text(20, 130, "a"),
        _text(120, 130, "b"),  # region (100,200): two columns
        _text(20, 230, "solo"),  # region (200,300): one column
        _text(20, 330, "c"),
        _text(120, 330, "d"),  # region (300,400): two columns
    ]
    regions = detect_table_regions(lines, items, page_width=200)
    assert len(regions) == 2


def test_an_item_between_the_rule_and_the_row_band_is_left_out_of_the_columns() -> None:
    """An item inside the rule pair but within the rule tolerance is not a column."""
    lines = [_line(0, 100, 200, 100), _line(0, 200, 200, 200)]
    items = [_text(20, 101, "edge"), _text(20, 130, "a"), _text(120, 130, "b")]
    regions = detect_table_regions(lines, items, page_width=200)
    assert len(regions) == 1


def test_row_separators_merge_into_one_region() -> None:
    """pc-4u7.42: rules that separate ROWS must not each become a table.

    Three stacked rows in two columns, with a full-width rule between each pair
    of rows: the consumer measured this shape as 30 chunks for one 30-row table
    (98 of 135 regions were single-row fragments on the full document).
    """
    lines = [_line(0, y, 200, y) for y in (100.0, 130.0, 160.0, 190.0)]
    items = [
        _text(20, 110, "a1"),
        _text(120, 110, "b1"),
        _text(20, 140, "a2"),
        _text(120, 140, "b2"),
        _text(20, 170, "a3"),
        _text(120, 170, "b3"),
    ]
    regions = detect_table_regions(lines, items, page_width=200)
    assert len(regions) == 1
    (_y0, _y1), rows = regions[0]
    assert rows == (("a1", "b1"), ("a2", "b2"), ("a3", "b3"))


def test_a_one_column_gap_closes_the_region() -> None:
    """A one-column band between rules is a break, not a table row: two regions."""
    lines = [_line(0, y, 200, y) for y in (100.0, 130.0, 160.0, 190.0)]
    items = [
        _text(20, 110, "a1"),
        _text(120, 110, "b1"),
        _text(20, 140, "section separator"),
        _text(20, 170, "a3"),
        _text(120, 170, "b3"),
    ]
    assert len(detect_table_regions(lines, items, page_width=200)) == 2


def test_a_moved_column_boundary_starts_a_new_region() -> None:
    """Columns that shift beyond COLUMN_MATCH_TOL are a different table."""
    lines = [_line(0, y, 200, y) for y in (100.0, 130.0, 160.0)]
    items = [
        _text(20, 110, "a1"),
        _text(120, 110, "b1"),  # columns centred at 35 / 135
        _text(20, 140, "a2"),
        _text(160, 140, "b2"),  # columns centred at 35 / 175 -> shifted
    ]
    assert len(detect_table_regions(lines, items, page_width=200)) == 2


def test_the_recorded_slice_merges_to_the_same_four_tables() -> None:
    """The merge must not change the slice: 4 tables, same column counts.

    Page 2 -> 3 regions (7 / 3 / 8 columns), page 3 -> 1 (5 columns), page 10's
    TOC candidates suppressed. The slice's tables were already single rule-pairs,
    so the merge is a no-op here — which is exactly why it is pinned: the stage
    exists for the ROW-separated tables the full document is full of, and a change
    that moves the slice's count is a signal.
    """
    items_doc = json.loads((_FIXTURES / "items.json").read_text(encoding="utf-8"))
    vec_doc = json.loads((_FIXTURES / "vec.json").read_text(encoding="utf-8"))
    per_page: dict[int, list[int]] = {}
    for index, page in enumerate(items_doc["pages"], start=1):
        vec_page = vec_doc["pages"][index - 1]
        items = [SimpleNamespace(x=it["x"], y=it["y"], text=it.get("text", ""), width=it.get("width", 0.0)) for it in (page.get("text_items") or [])]
        lines = [SimpleNamespace(**ln) for ln in (vec_page.get("vector_graphics") or {}).get("lines", [])]
        regions = detect_table_regions(lines, items, page_width=float(page.get("width") or 0.0))
        if regions:
            per_page[index] = [_columns_in_band(items, y0, y1) for (y0, y1), _rows in regions]

    assert sum(len(v) for v in per_page.values()) == MECHANISM_COUNTS_10_PAGE["merged_regions"] == 4
    assert per_page == {2: [7, 3, 8], 3: [5]}


def test_a_drifted_row_does_not_split_the_table() -> None:
    """pc-4u7.42 residual: one drifted row must not end the table.

    The consumer measured one ~30-row table arriving as 12 chunks, breaking at
    exactly the rows whose first cell is long and wrapped (``NB_ERIC_Field_GSM_``)
    — a wrapped cell moves that column's measured centre, so the boundary sits
    ~15-20 pt from where the model expects it. Comparing each pair against a frozen
    model split the table there; the run's model is the MEDIAN of what it has
    accepted, and a single drifted row moves the model instead of breaking the run.

    Here the second row's first cell centre drifted from 35 to 75 pt (an x shift
    standing in for the wrap): three columns, outer edges unchanged, one interior
    boundary off — still one table.
    """
    lines = [_line(0, y, 200, y) for y in (100.0, 130.0, 160.0, 190.0)]
    items = [
        _text(20, 110, "a1"),
        _text(100, 110, "b1"),
        _text(180, 110, "c1"),
        _text(60, 140, "a2-wrapped"),  # drifted centre
        _text(100, 140, "b2"),
        _text(180, 140, "c2"),
        _text(20, 170, "a3"),
        _text(100, 170, "b3"),
        _text(180, 170, "c3"),
    ]
    regions = detect_table_regions(lines, items, page_width=200)
    assert len(regions) == 1
    assert len(regions[0][1]) == 3  # three rows, one table


def test_a_numbered_row_continues_the_run_despite_column_mismatch() -> None:
    """pc-4u7.42 option 3: a numbered data row names its row of the table above.

    The consumer's evidence: page 57's row 1 measured 5 columns while row 2
    measured 6 (a wrapped cell added a detected item), and row 7 ("7 | No data
    set defined...") of the same numbered test-case table arrived as its own
    chunk. A first cell that is a data value (7, 6.3, 0.0001) continues the run;
    only a label first cell starts a new table.
    """
    lines = [_line(0, y, 200, y) for y in (100.0, 130.0, 160.0, 190.0)]
    items = [
        _text(20, 110, "1"),
        _text(100, 110, "1328"),
        _text(180, 110, "case one"),
        _text(20, 140, "2"),  # first cell is a data value
        _text(70, 140, "40"),  # wrapped cell: columns drifted AND count changed
        _text(100, 140, "b"),
        _text(180, 140, "case two"),
        _text(20, 170, "3"),
        _text(100, 170, "1328"),
        _text(180, 170, "case three"),
    ]
    regions = detect_table_regions(lines, items, page_width=200)
    assert len(regions) == 1
    assert len(regions[0][1]) == 3


def test_page19_stays_two_tables_via_the_caption_band() -> None:
    """Round 4: a column-aligned bold header EXTENDS (page 2's shaded row); the
    page-19 split comes from the caption band between the two tables.

    The partner rule means boldness alone never closes a run when the bold items
    sit inside the model's columns — the highlighted-row evidence. What keeps
    page 19 at two tables is its "Table 7 - Definition of..." caption: a bold
    single-column band with no partners, which closes the run.
    """
    lines = [_line(0, y, 200, y) for y in (100.0, 130.0, 160.0, 190.0, 220.0, 250.0)]
    items = [
        _text(20, 110, "1"),
        _text(100, 110, "1328"),
        _text(20, 140, "Frequency (Hz)"),  # bold-in-the-real-doc, column-aligned
        _text(100, 140, "Narrowband gain"),
        _text(20, 170, "50"),
        _text(100, 170, "1.5"),
        # caption band: one wide centred item, no column partners -> closes the run
        _text(60, 205, "Table 8 - Definition of narrowband terms"),
        _text(20, 235, "1"),
        _text(100, 235, "16000"),
    ]
    # The header row merges into the run above (partners); the caption band is
    # single-column and closes it: two tables.
    assert len(detect_table_regions(lines, items, page_width=200, separators=(140.0, 205.0))) == 2
    # Without separators the caption still closes it - a single-column band is
    # non-tabular on its own, which is why the caption mechanism is robust.
    assert len(detect_table_regions(lines, items, page_width=200)) == 2


def test_a_band_of_only_whitespace_items_does_not_continue_a_run() -> None:
    """A band whose every item is whitespace has no first cell to name a row.

    Its columns still count (geometry does not need text), so the run close falls
    to the continuation rule - which must decline, not crash, when there is no
    leftmost text item to read.
    """
    lines = [_line(0, y, 200, y) for y in (100.0, 130.0, 160.0)]
    items = [
        _text(20, 110, "1"),
        _text(100, 110, "1328"),
        _text(20, 140, "   "),
        _text(120, 140, "  "),  # columns drifted; every cell empty
    ]
    assert len(detect_table_regions(lines, items, page_width=200)) == 1


def test_a_curved_shape_page_yields_no_tables() -> None:
    """pc-4u7.42 round 4: curved vector shapes are figure content, not tables.

    Measured on the corpus: the flowchart/timing-diagram pages (Figures 5/6) carry
    1-12 curved shapes while every table page carries none - so one curved shape
    excludes the page's rule bands, which are the figure's boxes.
    """
    lines = [_line(0, 100, 200, 100), _line(0, 160, 200, 160)]
    items = [_text(20, 130, "a"), _text(120, 130, "b")]
    assert detect_table_regions(lines, items, page_width=200, shapes=(_shape(True),)) == []
    # The same geometry without curves is a table - the shapes are the signal.
    assert len(detect_table_regions(lines, items, page_width=200, shapes=(_shape(),))) == 1


def _shape(has_curve: bool = False) -> Any:
    return SimpleNamespace(bbox=(0.0, 0.0, 10.0, 10.0), stroke=False, fill=True, has_curve=has_curve)


def test_a_shaded_bold_row_with_column_partners_extends_the_run() -> None:
    """pc-4u7.42 round 4: page 2's highlighted P.800 row is a row, not a heading.

    A bold row whose placeable items sit inside the model's columns rejoins the
    table even though membership failed (the shaded row adds a wrapped label that
    shifts the measured columns). Boldness alone must not close a run.
    """
    lines = [_line(0, y, 200, y) for y in (100.0, 130.0, 160.0)]
    items = [
        _text(20, 110, "label"),
        _text(120, 110, "value"),
        _bold_text(20, 140, "Methods for objective"),  # bold, inside column 1
        _bold_text(60, 140, "assessment"),  # bold, inside column 1 (drifts edges -> membership fails)
        _bold_text(120, 140, "P.800"),  # bold, inside column 2
    ]
    assert len(detect_table_regions(lines, items, page_width=200, separators=(140.0,))) == 1


def test_a_bold_section_gap_taller_than_a_row_still_closes() -> None:
    """The partner rule is for row-height bands: a 70 pt gap is a section, not a row."""
    lines = [_line(0, y, 200, y) for y in (100.0, 130.0, 200.0, 230.0)]
    items = [
        _text(20, 110, "label"),
        _text(120, 110, "value"),
        _bold_text(20, 165, "A.2.2"),  # bold heading in a tall gap
        _bold_text(60, 165, "Conformance requirements"),
        _text(20, 215, "x"),
        _text(120, 215, "y"),
    ]
    assert len(detect_table_regions(lines, items, page_width=200, separators=(165.0,))) == 2


def test_partner_rule_edge_branches() -> None:
    """The three ways `_bold_row_has_column_partners` says no.

    A separator band holding no bold item at all (line 1), a wide wrapped label
    exempt from the placement test (line 2), and a small bold item whose centre
    falls outside every column (line 3) - the last is what keeps a bold line that
    ignores the table's columns classified as a heading.
    """
    model = (0.0, 85.0, 200.0)
    # No bold items in the band: no partners.
    assert _bold_row_has_column_partners(model, [_text(20, 140, "plain")], 130.0, 160.0) is False
    # A wide wrapped label (width > WRAPPED_LABEL_WIDTH) is exempt, and the small
    # bold item beside it places the row.
    assert (
        _bold_row_has_column_partners(
            model,
            [_bold_text(20, 140, "wide wrapped label", width=300.0), _bold_text(120, 140, "P.800")],
            130.0,
            160.0,
        )
        is True
    )
    # A small bold item outside every column (centred on the page, between the
    # model's columns but on no column's interior): no partners.
    assert _bold_row_has_column_partners(model, [_bold_text(83, 140, "x", width=4.0)], 130.0, 160.0) is False


def _bold_text(x: float, y: float, text: str, *, width: float = 30.0) -> Any:
    return SimpleNamespace(x=x, y=y, text=text, width=width, font_name="Times-Bold", font_size=10.0, font_weight=700)


def test_a_non_filled_rectangle_is_not_a_highlight() -> None:
    """Only FILLED wide+tall rects are highlights; a stroked box keeps its rules.

    The page-2 highlight is a filled rectangle; an unfilled (stroked) box of the
    same size is a border whose rules must survive - e.g. a boxed figure's frame
    on a page the curve test has not excluded.
    """
    lines = [_line(0, 100, 200, 100), _line(0, 130, 200, 130), _line(0, 160, 200, 160)]
    items = [_text(20, 115, "a"), _text(120, 115, "b"), _text(20, 145, "c"), _text(120, 145, "d")]
    stroked_box = SimpleNamespace(bbox=(0.0, 100.0, 200.0, 60.0), stroke=True, fill=False, has_curve=False)
    assert len(detect_table_regions(lines, items, page_width=200, shapes=(stroked_box,))) == 1


def _line(x1: float, y1: float, x2: float, y2: float, *, stroke: bool = True) -> Any:
    return SimpleNamespace(x1=x1, y1=y1, x2=x2, y2=y2, stroke=stroke, fill=False)


def _text(x: float, y: float, text: str, *, width: float = 30.0) -> Any:
    return SimpleNamespace(x=x, y=y, text=text, width=width)
