"""Item-level mapping for LiteParse PDFs: text items → lines → typed IR chunks.

A page's ``markdown`` is one blob; its ``text_items`` carry the per-run geometry
and font that an untagged PDF's actual structure lives in (measured on the
ITU-T P.863 corpus: 4 200 items → 2 929 lines → 227 headings → 576 elements for
the 80-page document, where the blob mapping emitted 78 paragraphs — one per
page).

The mapping is deliberately pure and liteparse-free: it takes anything with the
handful of attributes :class:`TextItemLike` names, so it is unit-testable
offline against recorded item dumps (``tests/backends/fixtures``) and cannot
import the engine by accident.

Pipeline (each step is what the measurements said was necessary):

1. **lines** — merge items sharing a baseline (``ITEM_Y_TOLERANCE``). Skipping
   this invented 436 "headings" out of letter-spaced cover text on the corpus,
   where the same-baseline merge halves it to 227: the merge is not optional.
2. **heading level** — classify a line by its *dominant* font, relative to the
   page's body size (``HEADING_RATIOS``) with the bold flag from ``font_weight``
   (falling back to the font name). Relative thresholds travel between
   documents; absolute ones did not.
3. **paragraphs** — merge consecutive body lines whose vertical gap is below
   ``PARAGRAPH_GAP_FACTOR`` × the page's typical gap.
4. **running headers/footers** — a line repeated verbatim across pages *and*
   sitting in the top/bottom margin band is a running header: markdown
   rendering strips them, the item list does not (``HEADER_BAND``). Needs the
   previous pages' texts, so the caller threads them in.
5. **TOC entries** — a line with a dot leader is a table-of-contents entry, not
   a heading, however bold it is (``_is_toc_entry``).
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from parsecraft.ir.models import ChunkKind, StructuredChunk

#: Two items belong to one line when their baselines sit within this many points.
ITEM_Y_TOLERANCE = 2.0
#: A heading is bold text this many times the page's body size (h1..h3); any
#: other bold line is h4 (a bold caption or a small subhead).
HEADING_RATIOS = (1.35, 1.15, 1.02)
#: Body lines whose gap exceeds this factor of the page's typical gap start a
#: new paragraph.
PARAGRAPH_GAP_FACTOR = 1.6
#: Top and bottom fraction of the page where a repeating line is a running header.
HEADER_BAND = 0.08
#: Bold is read from ``font_weight`` at or above this value, else from the name.
BOLD_WEIGHT = 600
#: A dot leader (or its ellipsis form) marks a table-of-contents entry.
_TOC_LEADER = re.compile(r"\.{3,}|…")
_WHITESPACE = re.compile(r"\s+")


@dataclass(slots=True)
class _Line:
    """One baseline's worth of items, with the font that dominates it."""

    y: float
    size: float
    bold: bool
    text: str = ""
    level: int = 0
    parts: list[TextItemLike] = field(default_factory=list)


class TextItemLike(Protocol):
    """The slice of one LiteParse ``TextItem`` the mapping reads."""

    text: str | None
    x: float | None
    y: float | None
    font_name: str | None
    font_size: float | None
    font_weight: int | None


def chunks_for_page(
    items: Sequence[TextItemLike],
    *,
    page_number: int,
    page_height: float = 0.0,
    seen_texts: set[str] | None = None,
) -> list[StructuredChunk]:
    """Typed chunks for one page: headings at their level, body merged to paragraphs.

    ``seen_texts`` carries every text already emitted on earlier pages of the
    same document; the function adds this page's texts to it, so one instance
    threads the whole document and running headers are dropped where they
    repeat. Passing ``None`` (the default) disables the running-header filter,
    which is what a single-page conversion wants.
    """
    lines = classify_lines(lines_of(items))
    kept: list[_Line] = []
    for line in lines:
        if not line.text:
            continue
        if seen_texts is not None and _is_running_header(line, page_height, seen_texts):
            continue
        kept.append(line)
        if seen_texts is not None:
            seen_texts.add(line.text)

    chunks: list[StructuredChunk] = []
    index = 0
    body_size = _body_size(lines)
    pending: list[_Line] = []

    def _flush() -> None:
        nonlocal index, pending
        if not pending:
            return
        chunks.append(_chunk(page_number, index, ChunkKind.PARAGRAPH, _paragraph_text(pending), reading_order=index))
        index += 1
        pending = []

    for line in kept:
        if line.level:
            _flush()
            chunks.append(
                _chunk(
                    page_number,
                    index,
                    ChunkKind.HEADING,
                    line.text,
                    reading_order=index,
                    heading_level=str(line.level),
                )
            )
            index += 1
            continue
        if pending and body_size > 0:
            previous = pending[-1]
            gaps = [b.y - a.y for a, b in zip(kept, kept[1:], strict=False) if b.y - a.y > 0]
            typical = statistics.median(gaps) if gaps else 0.0
            if typical > 0 and line.y - previous.y > typical * PARAGRAPH_GAP_FACTOR:
                _flush()
        pending.append(line)
    _flush()
    return chunks


def lines_of(items: Iterable[TextItemLike]) -> list[_Line]:
    """Merge items sharing a baseline into lines, keeping each line's dominant font.

    Items are ordered top-down, left-right; an item joins the current line when
    its baseline is within :data:`ITEM_Y_TOLERANCE`, and the largest font on the
    line decides its size and boldness (display headings are the tall runs).
    """
    ordered = sorted(items, key=lambda item: (_number(item.y), _number(item.x)))
    lines: list[_Line] = []
    for item in ordered:
        y = _number(item.y)
        size = _number(item.font_size)
        bold = _is_bold(item)
        if lines and abs(y - lines[-1].y) <= ITEM_Y_TOLERANCE:
            current = lines[-1]
            current.parts.append(item)
            current.y = (current.y + y) / 2
            if size > current.size:
                current.size = size
                current.bold = bold
            continue
        lines.append(_Line(y=y, size=size, bold=bold, parts=[item]))
    for line in lines:
        ordered_parts = sorted(line.parts, key=lambda item: _number(item.x))
        line.text = _WHITESPACE.sub(" ", " ".join(str(item.text or "") for item in ordered_parts)).strip()
    return lines


def _number(value: str | int | float | bool | None, default: float = 0.0) -> float:
    """Coerce a wire value to float; liteparse sends ``None`` for unknown geometry."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _is_bold(item: TextItemLike) -> bool:
    """``font_weight`` when the wire carries it, else a ``Bold`` font-name suffix.

    The CLI's JSON dump omits ``font_weight`` (the Python object has it), so the
    weight is read optionally and the font name is the fallback.
    """
    weight = getattr(item, "font_weight", None)
    if isinstance(weight, int) and not isinstance(weight, bool):
        return weight >= BOLD_WEIGHT
    return "bold" in (getattr(item, "font_name", None) or "").lower()


def classify_lines(lines: Sequence[_Line]) -> list[_Line]:
    """Assign each line its heading level in place (page-relative thresholds)."""
    body_size = _body_size(lines)
    for line in lines:
        line.level = _level_for(line, body_size)
    return list(lines)


def _body_size(lines: Sequence[_Line]) -> float:
    """The page's body size: the most common size among its non-bold lines."""
    sizes = [round(line.size, 1) for line in lines if not line.bold and line.size > 0]
    if not sizes:
        return 0.0
    return statistics.mode(sizes)


def _level_for(line: _Line, body_size: float) -> int:
    """``0`` for body text, ``1``..``4`` for headings (relative to the body size)."""
    if not line.bold or _is_toc_entry(line.text):
        return 0
    if body_size <= 0:
        return 4
    ratio = line.size / body_size
    for level, threshold in enumerate(HEADING_RATIOS, start=1):
        if ratio >= threshold:
            return level
    return 4


def _is_toc_entry(text: str) -> bool:
    """A dot leader means a contents entry: it must not become a heading."""
    return bool(_TOC_LEADER.search(text))


def _paragraph_text(lines: Sequence[_Line]) -> str:
    return " ".join(line.text for line in lines)


def _is_running_header(line: _Line, page_height: float, seen: set[str]) -> bool:
    """A line repeated from an earlier page inside the top/bottom margin band."""
    if line.text not in seen or page_height <= 0:
        return False
    band = page_height * HEADER_BAND
    return line.y <= band or line.y >= page_height - band


def _chunk(
    page_number: int,
    index: int,
    kind: ChunkKind,
    content: str,
    *,
    reading_order: int,
    heading_level: str | None = None,
) -> StructuredChunk:
    """One IR chunk with the backend's provenance and, for headings, its level."""
    metadata: dict[str, str] = {"backend": "liteparse"}
    if heading_level is not None:
        metadata["heading_level"] = heading_level
    return StructuredChunk(
        id=f"liteparse-p{page_number}-b{index}",
        kind=kind,
        content=content,
        page_number=page_number,
        reading_order=reading_order,
        metadata=metadata,
    )
