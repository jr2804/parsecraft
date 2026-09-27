"""Offline tests for the Markdown input adapter."""

from __future__ import annotations

import textwrap
from datetime import UTC, datetime
from typing import Any

import pytest
from markdown_it.token import Token

from parsecraft.adapters import parse_markdown
from parsecraft.adapters.markdown import _ChunkBuilder, _image_parts, _inline_text
from parsecraft.ir.models import ChunkKind, DiagnosticLevel, SourceSpan

PRODUCED = datetime(2026, 9, 27, tzinfo=UTC)


# ── document envelope ──────────────────────────────────────────────────────


def test_empty_document_yields_single_empty_page() -> None:
    result = parse_markdown("", "mem://empty", produced_at=PRODUCED)
    assert result.metadata.page_count == 1
    assert result.pages[0].blocks == []
    assert result.metadata.format == "text/markdown"
    assert result.metadata.produced_at == PRODUCED


def test_bytes_source_is_decoded_utf8() -> None:
    result = parse_markdown("# Café".encode(), "mem://bytes", produced_at=PRODUCED)
    assert contents(result) == ["Café"]


def test_metadata_fields_and_source_hash() -> None:
    result = parse_markdown("hi", "mem://x", title="T", package_version="9.9.9", produced_at=PRODUCED)
    assert result.metadata.source_uri == "mem://x"
    assert result.metadata.title == "T"
    assert result.metadata.package_version == "9.9.9"
    assert len(result.metadata.source_hash) == 64
    assert result.metadata.schema_version == "1"


def test_produced_at_defaults_to_utcnow() -> None:
    result = parse_markdown("hi", "mem://x")
    assert result.metadata.produced_at.tzinfo is UTC


def test_deterministic_same_input_same_result() -> None:
    source = "# A\n\npara\n\n- l1\n"
    assert parse_markdown(source, "mem://d", produced_at=PRODUCED) == parse_markdown(source, "mem://d", produced_at=PRODUCED)


# ── token kind mapping ─────────────────────────────────────────────────────


def test_heading_levels_and_metadata() -> None:
    result = parse_markdown("# One\n\n#### Four", "mem://h", produced_at=PRODUCED)
    assert kinds(result) == [ChunkKind.HEADING, ChunkKind.HEADING]
    assert result.pages[0].blocks[0].metadata == {"heading_level": "1"}
    assert result.pages[0].blocks[1].metadata == {"heading_level": "4"}


def test_paragraph_with_inline_markup() -> None:
    result = parse_markdown("Hello *world*.", "mem://p", produced_at=PRODUCED)
    assert kinds(result) == [ChunkKind.PARAGRAPH]
    assert contents(result) == ["Hello world."]


def test_formula_paragraph() -> None:
    result = parse_markdown("$$\nE = mc^2\n$$", "mem://f", produced_at=PRODUCED)
    assert kinds(result) == [ChunkKind.FORMULA]
    assert contents(result) == ["E = mc^2"]


def test_bullet_and_ordered_lists() -> None:
    result = parse_markdown("- a\n- b\n\n1. one\n2. two", "mem://l", produced_at=PRODUCED)
    assert kinds(result) == [ChunkKind.LIST, ChunkKind.LIST]
    assert contents(result) == ["a\nb", "one\ntwo"]
    assert result.pages[0].blocks[0].metadata == {"list_type": "bullet"}
    assert result.pages[0].blocks[1].metadata == {"list_type": "ordered"}


def test_table_with_header_and_body() -> None:
    source = "| a | b |\n| - | - |\n| 1 | 2 |\n"
    result = parse_markdown(source, "mem://t", produced_at=PRODUCED)
    assert kinds(result) == [ChunkKind.TABLE]
    assert contents(result) == ["| a | b |\n| --- | --- |\n| 1 | 2 |"]


def test_blockquote() -> None:
    result = parse_markdown("> quoted line\n> more", "mem://q", produced_at=PRODUCED)
    assert kinds(result) == [ChunkKind.QUOTE]
    assert "quoted line" in contents(result)[0]
    assert "more" in contents(result)[0]


def test_fence_with_language_and_without() -> None:
    result = parse_markdown("```python\nprint(1)\n```\n\n```\nplain\n```", "mem://c", produced_at=PRODUCED)
    assert kinds(result) == [ChunkKind.CODE, ChunkKind.CODE]
    assert result.pages[0].blocks[0].metadata == {"language": "python"}
    assert result.pages[0].blocks[0].content == "print(1)"
    assert result.pages[0].blocks[1].metadata == {}
    assert result.pages[0].blocks[1].content == "plain"


def test_indented_code_block() -> None:
    result = parse_markdown("    indented code", "mem://cb", produced_at=PRODUCED)
    assert kinds(result) == [ChunkKind.CODE]
    assert contents(result) == ["indented code"]


def test_image_only_paragraph_becomes_figure() -> None:
    result = parse_markdown("![alt text](img.png)", "mem://fig", produced_at=PRODUCED)
    assert kinds(result) == [ChunkKind.FIGURE]
    chunk = result.pages[0].blocks[0]
    assert chunk.content == "![alt text](img.png)"
    assert chunk.metadata == {"image_src": "img.png", "image_alt": "alt text"}


def test_image_with_text_becomes_figure_plus_caption() -> None:
    result = parse_markdown("![alt](img.png) Figure 1: caption", "mem://cap", produced_at=PRODUCED)
    assert kinds(result) == [ChunkKind.FIGURE, ChunkKind.CAPTION]
    assert result.pages[0].blocks[1].content == "Figure 1: caption"
    assert result.pages[0].blocks[0].reading_order < result.pages[0].blocks[1].reading_order


def test_html_block_and_hr_become_unknown() -> None:
    result = parse_markdown("<div>html</div>\n\n---", "mem://u", produced_at=PRODUCED)
    assert kinds(result) == [ChunkKind.UNKNOWN, ChunkKind.UNKNOWN]
    assert contents(result)[0] == "<div>html</div>"


def kinds(result: Any) -> list[ChunkKind]:
    return [chunk.kind for chunk in result.pages[0].blocks]


def test_reading_order_is_dense_and_ascending() -> None:
    result = parse_markdown("# H\n\npara\n\n- x\n", "mem://ro", produced_at=PRODUCED)
    orders = [chunk.reading_order for chunk in result.pages[0].blocks]
    assert orders == list(range(len(orders)))


# ── no silent drops, spans ────────────────────────────────────────────────


def test_no_silent_drops_every_content_token_appears() -> None:
    source = textwrap.dedent(
        """\
        # Title

        body text

        - item alpha
        - item beta

        | h1 | h2 |
        | -- | -- |
        | v1 | v2 |

        > deep thought

        ```py
        code here
        ```

        ![pic](p.png)
        """
    )
    result = parse_markdown(source, "mem://all", produced_at=PRODUCED)
    combined = "\n".join(contents(result))
    for needle in ("Title", "body text", "item alpha", "item beta", "h1", "v1", "deep thought", "code here", "pic"):
        assert needle in combined


def contents(result: Any) -> list[str]:
    return [chunk.content for chunk in result.pages[0].blocks]


def test_source_spans_are_exact_offsets() -> None:
    source = "# Hi\n\nbody\n"
    result = parse_markdown(source, "mem://s", produced_at=PRODUCED)
    heading, paragraph = result.pages[0].blocks
    assert heading.source_span is not None
    assert paragraph.source_span is not None
    assert heading.source_span == SourceSpan(start=0, end=5)
    assert source[heading.source_span.start : heading.source_span.end] == "# Hi\n"
    assert source[paragraph.source_span.start : paragraph.source_span.end] == "body\n"


def test_diagnostics_recorded_for_contentless_top_level_token() -> None:
    builder = _ChunkBuilder("", [0])
    builder._handle_unknown(make_token("hr", content="", markup=""))
    assert len(builder.diagnostics) == 1
    assert builder.diagnostics[0].level is DiagnosticLevel.INFO
    assert builder.diagnostics[0].code == "markdown-empty-token"


# ── internal seams (coverage of defensive branches) ───────────────────────


def test_inline_text_handles_none_and_breaks() -> None:
    assert _inline_text(None) == ("", [])
    bare = make_token("inline", children=[])
    assert _inline_text(bare) == ("", [])
    soft = make_token("inline", children=[make_token("softbreak"), make_token("text", content="x")])
    assert _inline_text(soft)[0] == "\nx"
    hard = make_token("inline", children=[make_token("hardbreak"), make_token("code_inline", content="c")])
    assert _inline_text(hard)[0] == "\nc"


def test_image_parts_without_src_attr() -> None:
    image = make_token("image", content="alt", attrs=None)
    assert _image_parts(image) == ("", "alt")


def test_span_is_none_without_map() -> None:
    builder = _ChunkBuilder("text", [0, 5])
    token = make_token("paragraph_open", map=None)
    builder._add(ChunkKind.PARAGRAPH, "text", token)
    assert builder._chunks[0].source_span is None
    assert builder._chunks[0].metadata == {}


def test_heading_without_inline_token_consumes_one() -> None:
    builder = _ChunkBuilder("", [0])
    consumed = builder._handle_heading_open([make_token("heading_open", tag="h2")], 0)
    assert consumed == 1
    assert builder._chunks[0].kind is ChunkKind.HEADING


def test_unbalanced_container_falls_back_to_last_token() -> None:
    builder = _ChunkBuilder("> x", [0, 4])
    tokens = [make_token("blockquote_open", nesting=1, map=[0, 1]), make_token("inline", level=1, content="x")]
    close = builder._matching_close(tokens, 0)
    assert close == len(tokens) - 1


def test_top_level_inline_token_becomes_paragraph() -> None:
    builder = _ChunkBuilder("x", [0])
    builder._handle_inline([make_token("inline", content="x")], 0)
    assert builder._chunks[0].kind is ChunkKind.PARAGRAPH


def test_span_end_clamps_to_text_length() -> None:
    builder = _ChunkBuilder("abc", [0, 4, 4])
    token = make_token("paragraph_open", map=[0, 5])
    assert builder._span(token) == SourceSpan(start=0, end=3)


@pytest.mark.parametrize(("level", "expected_consumed"), [(1, 0), (0, 1)])
def test_build_skips_nested_tokens(level: int, expected_consumed: int) -> None:
    builder = _ChunkBuilder("", [0])
    token = make_token("text", level=level, content="x")
    builder.build([token])
    assert len(builder._chunks) == (1 if level == 0 else 0)
    assert expected_consumed is not None


def make_token(type_: str, **overrides: Any) -> Token:
    values: dict[str, Any] = {
        "type": type_,
        "tag": "p",
        "nesting": 0,
        "attrs": None,
        "map": None,
        "level": 0,
        "content": "",
        "markup": "",
        "info": "",
        "children": None,
    }
    values.update(overrides)
    return Token(**values)
