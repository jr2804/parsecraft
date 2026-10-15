"""Item-level liteparse mapping: lines, heading levels, paragraphs, headers, TOC."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from parsecraft.backends.liteparse._items import (
    chunks_for_page,
    classify_lines,
    lines_of,
)
from parsecraft.ir.models import ChunkKind

#: Recorded `lit parse --extract-text-metadata` dumps, if the consumer staged them.
_FIXTURES = Path("tmp/liteparse-fixtures")


def test_same_baseline_items_merge_into_one_line() -> None:
    """Letter-spaced cover text arrives as many items on one baseline, not many lines."""
    lines = lines_of([item("I", x=0.0, y=84.0, size=12.0), item("T", x=4.0, y=84.5), item("U", x=8.0, y=84.2)])
    assert len(lines) == 1
    assert lines[0].text == "I T U"


def test_distinct_baselines_stay_separate_and_keep_the_dominant_font() -> None:
    lines = lines_of([item("body", y=100.0), item("TITLE", y=80.0, size=18.0, bold=True)])
    assert [line.text for line in lines] == ["TITLE", "body"]  # top-down
    assert lines[0].size == 18.0
    assert lines[0].bold is True


def test_heading_levels_are_relative_to_the_page_body_size() -> None:
    items = [
        item("h1", y=10.0, size=18.0, bold=True),
        item("h3", y=30.0, size=11.0, bold=True),
        item("plain", y=50.0, size=10.0),
        item("body", y=62.0, size=10.0),
        item("body", y=74.0, size=10.0),
    ]
    levels = {line.text: line.level for line in classify_lines(lines_of(items))}
    assert levels["h1"] == 1
    assert levels["h3"] == 3
    assert levels["plain"] == 0


def test_a_dot_leader_is_a_contents_entry_not_a_heading() -> None:
    """TOC entries are bold and large; they must stay body text."""
    lines = classify_lines(lines_of([item("Summary .......... 3", y=20.0, size=16.0, bold=True)]))
    assert lines[0].level == 0


def test_paragraphs_split_on_an_above_typical_vertical_gap() -> None:
    items = [
        item("first line", y=10.0),
        item("second line", y=22.0),
        item("third line", y=34.0),
        item("far away", y=90.0),
    ]
    chunks = chunks_for_page(items, page_number=1)
    assert [chunk.kind for chunk in chunks] == [ChunkKind.PARAGRAPH, ChunkKind.PARAGRAPH]
    assert chunks[0].content == "first line second line third line"
    assert chunks[1].content == "far away"


def test_headings_stay_separate_chunks_with_their_level() -> None:
    items = [item("Section", y=10.0, size=18.0, bold=True), item("body", y=30.0), item("body", y=42.0)]
    chunks = chunks_for_page(items, page_number=2)
    assert [chunk.kind for chunk in chunks] == [ChunkKind.HEADING, ChunkKind.PARAGRAPH]
    assert chunks[0].metadata["heading_level"] == "1"
    assert [chunk.reading_order for chunk in chunks] == [0, 1]
    assert chunks[0].id == "liteparse-p2-b0"


def test_a_running_header_repeated_in_the_margin_band_is_dropped() -> None:
    seen: set[str] = set()
    first = chunks_for_page(
        [item("Recommendation ITU-T P.863", y=30.0, size=14.0, bold=True), item("body", y=200.0)],
        page_number=1,
        page_height=800.0,
        seen_texts=seen,
    )
    assert ChunkKind.HEADING in {chunk.kind for chunk in first}  # first page keeps it

    second = chunks_for_page(
        [item("Recommendation ITU-T P.863", y=30.0, size=14.0, bold=True), item("body two", y=200.0)],
        page_number=2,
        page_height=800.0,
        seen_texts=seen,
    )
    assert [chunk.content for chunk in second] == ["body two"]


def test_the_mapping_is_deterministic() -> None:
    items = [item("b", y=20.0), item("a", y=10.0), item("c", y=30.0)]
    first = [chunk.model_dump() for chunk in chunks_for_page(items, page_number=1)]
    second = [chunk.model_dump() for chunk in chunks_for_page(list(reversed(items)), page_number=1)]
    assert first == second


@pytest.mark.skipif(not (_FIXTURES / "p863_rec_p1-10.items.json").exists(), reason="consumer fixtures not staged")
def test_the_recorded_10_page_slice_reproduces_the_measured_counts() -> None:
    """Acceptance against the recorded dump: 435 items → 265 lines → 26 headings.

    The dump is `lit parse <pdf> --format json --extract-text-metadata` from
    liteparse 2.16.0 on the polqa-re host; it is staged under a gitignored path,
    so this test skips where it is absent.
    """
    document = json.loads((_FIXTURES / "p863_rec_p1-10.items.json").read_text(encoding="utf-8"))
    pages = document.get("pages") or []
    total_items = 0
    total_lines = 0
    for page in pages:
        raw = [SimpleNamespace(**item) for item in (page.get("text_items") or [])]
        total_items += len(raw)
        total_lines += len(lines_of(raw))
    assert total_items == 435
    assert total_lines == 265

    # Unfiltered, the mapping reproduces the consumer's prototype exactly:
    # 26 headings (that measurement is the acceptance datum).
    unfiltered = _count(pages, seen=None)
    assert unfiltered == (26, 35)

    # Filtered — the production behaviour — drops exactly the two running
    # headers the prototype's author flagged (the repeated
    # "Recommendation ITU-T P.863"), leaving 24 headings / 59 elements.
    filtered = _count(pages, seen=set())
    assert filtered == (24, 35)


def _count(pages: list[dict[str, Any]], *, seen: set[str] | None) -> tuple[int, int]:
    """``(headings, paragraphs)`` for the recorded pages; ``seen=None`` disables the filter."""
    headings = 0
    paragraphs = 0
    for index, page in enumerate(pages, start=1):
        raw = [SimpleNamespace(**item) for item in (page.get("text_items") or [])]
        chunks = chunks_for_page(raw, page_number=index, page_height=float(page.get("height") or 0.0), seen_texts=seen)
        headings += sum(1 for chunk in chunks if chunk.kind is ChunkKind.HEADING)
        paragraphs += sum(1 for chunk in chunks if chunk.kind is ChunkKind.PARAGRAPH)
    return headings, paragraphs


def item(text: str, *, x: float = 0.0, y: float = 0.0, size: float = 10.0, bold: bool = False) -> Any:
    """One LiteParse-shaped text item (the mapping reads attributes, not a class)."""
    return SimpleNamespace(
        text=text,
        x=x,
        y=y,
        font_name="Times-BoldMT" if bold else "TimesNewRomanPSMT",
        font_size=size,
        font_weight=700 if bold else 400,
    )
