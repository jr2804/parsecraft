"""Layout-stage parsing for the native MinerU2.5 protocol — pure functions.

The wire grammar, normalization rules, dedupe, and container suppression are
pinned here against hand-written model outputs (offline, no model involved).
"""

from __future__ import annotations

from parsecraft.backends.mineru._layout import (
    LayoutBlock,
    _covered,
    _iou,
    dedupe_blocks,
    parse_layout,
    strip_covered,
)


def test_parse_layout_basic_block() -> None:
    text = "<|box_start|>100 100 400 200<|box_end|><|ref_start|>text<|ref_end|>hello"
    blocks = parse_layout(text)
    assert blocks == [LayoutBlock(type="text", x0=0.1, y0=0.1, x1=0.4, y1=0.2, angle=0, merge_prev=False)]


def test_parse_layout_coordinates_become_fractions() -> None:
    blocks = parse_layout("<|box_start|>0 0 1000 1000<|box_end|><|ref_start|>text<|ref_end|>")
    assert (blocks[0].x0, blocks[0].y0, blocks[0].x1, blocks[0].y1) == (0.0, 0.0, 1.0, 1.0)


def test_parse_layout_unknown_ref_becomes_image() -> None:
    blocks = parse_layout("<|box_start|>10 10 20 20<|box_end|><|ref_start|>unknown<|ref_end|>")
    assert blocks[0].type == "image"


def test_parse_layout_drops_types_outside_the_protocol() -> None:
    blocks = parse_layout("<|box_start|>10 10 20 20<|box_end|><|ref_start|>inline_formula<|ref_end|>")
    assert blocks == []


def test_parse_layout_drops_zero_area_boxes() -> None:
    blocks = parse_layout(
        "<|box_start|>100 100 100 200<|box_end|><|ref_start|>text<|ref_end|>"  # zero width
        "<|box_start|>100 100 200 100<|box_end|><|ref_start|>text<|ref_end|>"  # zero height
        "<|box_start|>10 10 90 90<|box_end|><|ref_start|>text<|ref_end|>"
    )
    assert len(blocks) == 1


def test_parse_layout_orders_swapped_corners() -> None:
    blocks = parse_layout("<|box_start|>400 200 100 100<|box_end|><|ref_start|>text<|ref_end|>")
    assert (blocks[0].x0, blocks[0].y0, blocks[0].x1, blocks[0].y1) == (0.1, 0.1, 0.4, 0.2)


def test_parse_layout_rotation_tokens_map_to_degrees() -> None:
    text = (
        "<|box_start|>10 10 30 30<|box_end|><|ref_start|>text<|ref_end|><|rotate_right|>"
        "<|box_start|>40 10 60 30<|box_end|><|ref_start|>text<|ref_end|><|rotate_left|>"
        "<|box_start|>70 10 90 30<|box_end|><|ref_start|>text<|ref_end|><|rotate_down|>"
        "<|box_start|>10 40 30 60<|box_end|><|ref_start|>text<|ref_end|><|rotate_diagonal|>"
    )
    angles = [block.angle for block in parse_layout(text)]
    assert angles == [90, 270, 180, 0]  # unknown direction falls back to upright


def test_parse_layout_merge_marker_only_flags_text() -> None:
    text = (
        "<|box_start|>10 10 30 30<|box_end|><|ref_start|>text<|ref_end|>tail txt_contd_tgt here"
        "<|box_start|>40 10 60 30<|box_end|><|ref_start|>table<|ref_end|>tail txt_contd_tgt here"
    )
    blocks = parse_layout(text)
    assert blocks[0].merge_prev is True
    assert blocks[1].merge_prev is False


def test_parse_layout_emission_order_preserved() -> None:
    text = (
        "<|box_start|>10 10 30 30<|box_end|><|ref_start|>footer<|ref_end|>"
        "<|box_start|>40 10 60 30<|box_end|><|ref_start|>title<|ref_end|>"
        "<|box_start|>70 10 90 30<|box_end|><|ref_start|>text<|ref_end|>"
    )
    assert [block.type for block in parse_layout(text)] == ["footer", "title", "text"]


def test_dedupe_blocks_drops_same_type_overlap() -> None:
    kept = dedupe_blocks([_block(), _block(x0=0.11), _block(kind="table")])
    assert [(block.type, block.x0) for block in kept] == [("text", 0.1), ("table", 0.1)]


def test_dedupe_blocks_keeps_same_geometry_different_type_or_angle() -> None:
    kept = dedupe_blocks([_block(), _block(kind="image"), _block(angle=90)])
    assert len(kept) == 3


def test_dedupe_blocks_keeps_low_overlap() -> None:
    kept = dedupe_blocks([_block(x0=0.0, x1=0.2), _block(x0=0.5, x1=0.7)])
    assert len(kept) == 2


def test_strip_covered_drops_table_internal_repeats() -> None:
    table = _block(kind="table", x0=0.0, y0=0.0, x1=0.5, y1=1.0)
    inside = _block(x0=0.05, y0=0.05, x1=0.45, y1=0.95)
    straddling = _block(x0=0.4, y0=0.0, x1=0.9, y1=1.0)  # mostly outside the table
    kept = strip_covered([table, inside, straddling, _block(kind="header")])
    assert [block.type for block in kept] == ["table", "text", "header"]


def test_strip_covered_drops_caption_inside_image_container() -> None:
    image = _block(kind="image", x0=0.0, y0=0.0, x1=1.0, y1=1.0)
    caption = _block(kind="image_caption", x0=0.1, y0=0.1, x1=0.9, y1=0.9)
    kept = strip_covered([image, caption, _block(kind="text", x0=0.0, x1=0.1, y0=0.0, y1=0.1)])
    assert [block.type for block in kept] == ["image", "text"]


def test_iou_zero_for_disjoint_blocks() -> None:
    assert _iou(_block(x0=0.0, x1=0.1), _block(x0=0.5, x1=0.6)) == 0.0


def test_iou_zero_when_union_collapses() -> None:
    # Degenerate blocks cannot survive parsing, but the helper must not divide
    # by zero when handed one directly.
    assert _iou(_block(x0=0.1, x1=0.1), _block(x0=0.1, x1=0.1)) == 0.0


def test_covered_false_for_degenerate_inner() -> None:
    assert _covered(_block(x0=0.1, x1=0.1), _block()) is False


def test_covered_ratio_boundary() -> None:
    inner = _block(x0=0.0, x1=1.0, y0=0.0, y1=1.0)
    # Exactly 90% coverage counts as covered (>= boundary, upstream semantics).
    outer = _block(x0=0.0, x1=0.9, y0=0.0, y1=1.0)
    assert _covered(inner, outer) is True


def _block(
    kind: str = "text",
    x0: float = 0.1,
    y0: float = 0.1,
    x1: float = 0.4,
    y1: float = 0.2,
    angle: int = 0,
    merge_prev: bool = False,
) -> LayoutBlock:
    return LayoutBlock(type=kind, x0=x0, y0=y0, x1=x1, y1=y1, angle=angle, merge_prev=merge_prev)
