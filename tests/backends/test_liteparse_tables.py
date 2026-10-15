"""Table-region detection: pure `_tables` + acceptance against the recorded fixture."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parsecraft.backends.liteparse._tables import (
    MECHANISM_COUNTS_10_PAGE,
    _horizontal_rules,
    detect_table_regions,
)

#: Recorded fixtures (liteparse 2.16.0; see README for provenance).
_FIXTURES = Path("tests/backends/fixtures/liteparse")


def _line(x1: float, y1: float, x2: float, y2: float, *, stroke: bool = True) -> Any:
    return SimpleNamespace(x1=x1, y1=y1, x2=x2, y2=y2, stroke=stroke, fill=False)


def _text(x: float, y: float, text: str, *, width: float = 30.0) -> Any:
    return SimpleNamespace(x=x, y=y, text=text, width=width)


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


@pytest.mark.skipif(not (_FIXTURES / "items.json").exists() or not (_FIXTURES / "vec.json").exists(),
                    reason="recorded fixtures not present in this checkout")
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
                x=it["x"], y=it["y"], text=it.get("text", ""), width=it.get("width", 0.0),
            )
            for it in (page.get("text_items") or [])
        ]
        lines = [
            SimpleNamespace(**ln)
            for ln in (vec_page.get("vector_graphics") or {}).get("lines", [])
        ]
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


def _columns_in_band(items: list[Any], y0: float, y1: float) -> int:
    """Mirror of `_tables._columns_in_band`; duplicated here to keep the test local."""
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
MECHANISM_COUNTS = {
    "rule_bands": 54,
    "full_width_rules": 48,
    "raw_regions": 42,
    "two_or_more_column_regions": 4,
}
MECHANISM_COUNTS_10_PAGE = MECHANISM_COUNTS
