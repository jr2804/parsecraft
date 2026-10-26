"""Layout-stage parsing for the native MinerU2.5 protocol — pure Python.

Parses the layout model's tagged output into typed blocks. The wire protocol
(``<|box_start|>x1 y1 x2 y2<|box_end|><|ref_start|>type<|ref_end|>`` with
optional rotation and merge flags) and the normalization rules (0–1000
coordinates, swap fix, zero-area rejection, IoU dedupe, table-internal
suppression) are ported from ``mineru-vl-utils`` (Apache-2.0, MinerU Team) —
see ADR-0007 for the attribution and licence notes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The layout model's tagged output grammar (one block per match).
_LAYOUT_PATTERN = re.compile(
    r"<\|box_start\|>(\d+)\s+(\d+)\s+(\d+)\s+(\d+)<\|box_end\|>"
    r"<\|ref_start\|>(\w+?)<\|ref_end\|>"
    r"(?:(<\|rotate_(?:up|right|down|left)\|>))?"
    r"(.*?)(?=<\|box_start\|>|$)",
    re.DOTALL,
)

#: Rotation token direction → clockwise degrees.
_ANGLES: dict[str, int] = {"up": 0, "right": 90, "down": 180, "left": 270}

#: Block types the layout model is specified to emit (from the checkpoint's
#: protocol). Anything else in the ref slot is dropped, not guessed at.
KNOWN_TYPES: frozenset[str] = frozenset(
    {
        "text",
        "title",
        "doc_title",
        "paragraph_title",
        "table",
        "equation",
        "formula_number",
        "code",
        "algorithm",
        "aside_text",
        "ref_text",
        "index",
        "phonetic",
        "list_item",
        "list",
        "table_caption",
        "image_caption",
        "code_caption",
        "caption",
        "table_footnote",
        "image_footnote",
        "footnote",
        "header",
        "footer",
        "page_number",
        "page_footnote",
        "image",
        "chart",
        "image_block",
        "equation_block",
        "unknown",
    }
)

#: Types that never get a content-extraction pass (upstream skip list): the
#: container blocks whose content is assembled from their children.
SKIP_EXTRACTION: frozenset[str] = frozenset({"list", "equation_block", "image_block"})

#: Text-ish types that a covering table suppresses (table-internal repeats).
_TABLE_SUPPRESSED: frozenset[str] = frozenset({"text", "equation", "list_item"})

#: Caption types a covering image/chart suppresses (duplicated captions).
_IMAGE_SUPPRESSED: frozenset[str] = frozenset({"image_caption"})

#: Container types that cover captions.
_IMAGE_CONTAINERS: frozenset[str] = frozenset({"image", "chart", "image_block"})

#: Merge marker the text-recognition tail may carry: this block continues the
#: previous text block (truncated-paragraph protocol).
_MERGE_MARKER = "txt_contd_tgt"

#: IoU above which two same-type blocks are duplicates (upstream threshold).
_DUPLICATE_IOU = 0.9

#: Coverage above which a block counts as inside a container (upstream).
_COVERED_RATIO = 0.9

#: Layout coordinates are integers in a 0–1000 space.
_LAYOUT_COORDINATES = 1000.0


@dataclass(frozen=True)
class LayoutBlock:
    """One layout block: type, normalized bbox (fractions 0–1), rotation."""

    type: str
    x0: float
    y0: float
    x1: float
    y1: float
    angle: int
    merge_prev: bool


def parse_layout(text: str) -> list[LayoutBlock]:
    """Parse the layout model's output into blocks, in emission order.

    Normalization per the checkpoint protocol: 0–1000 coordinates become
    fractions, swapped corners are ordered, zero-area boxes and unknown ref
    types are dropped, ``unknown`` refs become ``image``, and a text tail
    carrying the merge marker flags ``merge_prev``.
    """
    blocks: list[LayoutBlock] = []
    for match in _LAYOUT_PATTERN.finditer(text):
        raw_x0, raw_y0, raw_x1, raw_y1, ref_type, rotate_token, tail = match.groups()
        ref_type = "image" if ref_type == "unknown" else ref_type
        if ref_type not in KNOWN_TYPES:
            continue
        x0, y0 = int(raw_x0) / _LAYOUT_COORDINATES, int(raw_y0) / _LAYOUT_COORDINATES
        x1, y1 = int(raw_x1) / _LAYOUT_COORDINATES, int(raw_y1) / _LAYOUT_COORDINATES
        if x0 == x1 or y0 == y1:
            continue
        x0, x1 = min(x0, x1), max(x0, x1)
        y0, y1 = min(y0, y1), max(y0, y1)
        direction = _rotate_direction(rotate_token) if rotate_token else "up"
        blocks.append(
            LayoutBlock(
                type=ref_type,
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                angle=_ANGLES[direction],
                merge_prev=ref_type == "text" and _MERGE_MARKER in tail,
            )
        )
    return dedupe_blocks(blocks)


def _rotate_direction(rotate_token: str) -> str:
    """The direction word of a ``<|rotate_…|>`` token (unknown → up)."""
    inner = rotate_token.removeprefix("<|rotate_").removesuffix("|>")
    return inner if inner in _ANGLES else "up"


def dedupe_blocks(blocks: list[LayoutBlock]) -> list[LayoutBlock]:
    """Drop same-type/same-angle blocks that overlap an earlier one (IoU > 0.9)."""
    kept: list[LayoutBlock] = []
    for block in blocks:
        duplicate = any(earlier.type == block.type and earlier.angle == block.angle and _iou(earlier, block) > _DUPLICATE_IOU for earlier in kept)
        if not duplicate:
            kept.append(block)
    return kept


def strip_covered(blocks: list[LayoutBlock]) -> list[LayoutBlock]:
    """Drop text/equation blocks ≥90% covered by a table, and captions ≥90%
    covered by an image/chart container — the layout model repeats content
    inside container boxes, and the repeats are noise, not content.
    """
    tables = [block for block in blocks if block.type == "table"]
    images = [block for block in blocks if block.type in _IMAGE_CONTAINERS]
    kept: list[LayoutBlock] = []
    for block in blocks:
        if block.type in _TABLE_SUPPRESSED and any(_covered(block, table) for table in tables):
            continue
        if block.type in _IMAGE_SUPPRESSED and any(_covered(block, image) for image in images):
            continue
        kept.append(block)
    return kept


def _area(block: LayoutBlock) -> float:
    return max(0.0, (block.x1 - block.x0) * (block.y1 - block.y0))


def _intersection(a: LayoutBlock, b: LayoutBlock) -> float:
    width = min(a.x1, b.x1) - max(a.x0, b.x0)
    height = min(a.y1, b.y1) - max(a.y0, b.y0)
    return max(0.0, width) * max(0.0, height)


def _iou(a: LayoutBlock, b: LayoutBlock) -> float:
    union = _area(a) + _area(b) - _intersection(a, b)
    return _intersection(a, b) / union if union > 0 else 0.0


def _covered(inner: LayoutBlock, outer: LayoutBlock) -> bool:
    """True when ≥90% of ``inner``'s area lies inside ``outer``."""
    inner_area = _area(inner)
    if inner_area <= 0:
        return False
    return _intersection(inner, outer) / inner_area >= _COVERED_RATIO
