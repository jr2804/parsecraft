"""Deterministic Markdown projection — every kind, no silent drops."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from parsecraft.ir import (
    ChunkKind,
    DocumentMetadata,
    DocumentResult,
    PageResult,
    StructuredChunk,
    to_markdown,
)
from parsecraft.ir.markdown import render_block

_SHA = "b" * 64


@pytest.mark.parametrize(
    ("kind", "content", "metadata", "expected"),
    [
        (ChunkKind.PARAGRAPH, "plain text", {}, "plain text"),
        (ChunkKind.LIST, "- one\n- two", {}, "- one\n- two"),
        (ChunkKind.TABLE, "| a | b |\n| - | - |", {}, "| a | b |\n| - | - |"),
        (ChunkKind.FIGURE, "diagram", {}, "diagram"),
        (ChunkKind.CAPTION, "figure 1: a diagram", {}, "figure 1: a diagram"),
        (ChunkKind.UNKNOWN, "mystery", {}, "mystery"),
        (ChunkKind.HEADING, "Title", {"heading_level": "1"}, "# Title"),
        (ChunkKind.HEADING, "Title", {"heading_level": "2"}, "## Title"),
        (ChunkKind.HEADING, "Title", {}, "## Title"),
        (ChunkKind.HEADING, "Title", {"heading_level": "9"}, "## Title"),
        (ChunkKind.HEADING, "Title", {"heading_level": "0"}, "## Title"),
        (ChunkKind.HEADING, "Title", {"heading_level": "x"}, "## Title"),
        (ChunkKind.CODE, "print(1)", {}, "```\nprint(1)\n```"),
        (ChunkKind.CODE, "print(1)", {"language": "python"}, "```python\nprint(1)\n```"),
        (ChunkKind.FORMULA, "E = mc^2", {}, "$$\nE = mc^2\n$$"),
        (ChunkKind.QUOTE, "line one\nline two", {}, "> line one\n> line two"),
        (ChunkKind.QUOTE, "a\n\nb", {}, "> a\n>\n> b"),
        (ChunkKind.HEADER, "ACME Corp", {}, "<!-- header: ACME Corp -->"),
        (ChunkKind.FOOTER, "page footer", {}, "<!-- footer: page footer -->"),
    ],
)
def test_every_chunk_kind_renders_deterministically(
    kind: ChunkKind,
    content: str,
    metadata: dict[str, str],
    expected: str,
) -> None:
    assert render_block(_chunk(kind, content, **metadata)) == expected


def test_header_footer_cannot_break_out_of_comment() -> None:
    rendered = render_block(_chunk(ChunkKind.HEADER, "evil --> drop"))
    assert rendered == "<!-- header: evil -- > drop -->"
    assert rendered.count("-->") == 1


def test_document_projection_has_page_marker_and_trailing_newline() -> None:
    doc = _document(_chunk(ChunkKind.PARAGRAPH, "body"))
    markdown = to_markdown(doc)
    assert markdown.startswith("<!-- page 1 -->\n\nbody\n")
    assert markdown.endswith("\n")


def test_empty_document_renders_empty_string() -> None:
    doc = DocumentResult(metadata=_metadata(page_count=0))
    assert to_markdown(doc) == ""


def test_page_without_blocks_renders_marker_only() -> None:
    doc = DocumentResult(metadata=_metadata(page_count=1), pages=[PageResult(page_number=1)])
    assert to_markdown(doc) == "<!-- page 1 -->\n"


def test_projection_is_deterministic_across_calls() -> None:
    doc = _document(
        _chunk(ChunkKind.HEADING, "H", heading_level="3"),
        _chunk(ChunkKind.PARAGRAPH, "p"),
    )
    assert to_markdown(doc) == to_markdown(doc)


def test_no_silent_drops_every_content_appears() -> None:
    chunks = [
        _chunk(ChunkKind.HEADING, "unique-heading-text"),
        _chunk(ChunkKind.PARAGRAPH, "unique-paragraph-text"),
        _chunk(ChunkKind.TABLE, "unique-table-text"),
        _chunk(ChunkKind.FORMULA, "unique-formula-text"),
        _chunk(ChunkKind.CODE, "unique-code-text"),
        _chunk(ChunkKind.HEADER, "unique-header-text"),
        _chunk(ChunkKind.FOOTER, "unique-footer-text"),
    ]
    markdown = to_markdown(_document(*chunks))
    for chunk in chunks:
        assert chunk.content in markdown


def test_no_silent_drops_nested_children() -> None:
    grandchild = _node("gc", "CHARLIE-GRANDCHILD")
    child = _node("child", "BRAVO-CHILD", children=[grandchild])
    sibling = _node("sibling", "DELTA-SIBLING")
    parent = _node("parent", "ALPHA-PARENT", children=[child, sibling])
    markdown = to_markdown(_document(parent))
    for content in ("ALPHA-PARENT", "BRAVO-CHILD", "CHARLIE-GRANDCHILD", "DELTA-SIBLING"):
        assert markdown.count(content) == 1, content
    # Depth-first: parent, then its subtree, in declaration order.
    order = [markdown.index(text) for text in ("ALPHA-PARENT", "BRAVO-CHILD", "CHARLIE-GRANDCHILD", "DELTA-SIBLING")]
    assert order == sorted(order)


def test_nested_children_keep_their_own_chunk_kind() -> None:
    child = _node("child", "Nested title", kind=ChunkKind.HEADING, metadata={"heading_level": "4"})
    parent = _node("parent", "container", children=[child])
    markdown = to_markdown(_document(parent))
    assert "#### Nested title" in markdown
    assert "container" in markdown


def test_empty_content_container_renders_only_children() -> None:
    first = _node("first", "FIRST-CHILD")
    second = _node("second", "SECOND-CHILD")
    container = _node("container", "", children=[first, second])
    markdown = to_markdown(_document(container))
    assert markdown.count("FIRST-CHILD") == 1
    assert markdown.count("SECOND-CHILD") == 1
    assert "\n\n\n" not in markdown  # no stray blank block for the empty container


def test_empty_leaf_block_renders_page_marker_only() -> None:
    empty = _node("empty", "")
    markdown = to_markdown(_document(empty))
    assert markdown == "<!-- page 1 -->\n"


def test_projection_is_byte_identical_for_equal_documents() -> None:
    first = _document(
        _node("a", "Alpha", kind=ChunkKind.HEADING, metadata={"heading_level": "2"}),
        _node("b", "Beta", children=[_node("b-1", "nested beta")]),
    )
    second = _document(
        _node("a", "Alpha", kind=ChunkKind.HEADING, metadata={"heading_level": "2"}),
        _node("b", "Beta", children=[_node("b-1", "nested beta")]),
    )
    assert to_markdown(first).encode() == to_markdown(second).encode()


def test_every_block_of_every_page_appears_exactly_once() -> None:
    pages = [
        PageResult(
            page_number=page_number,
            blocks=[
                _node(f"p{page_number}-a", f"page-{page_number}-block-a", page=page_number, order=0),
                _node(f"p{page_number}-b", f"page-{page_number}-block-b", page=page_number, order=1),
            ],
        )
        for page_number in (1, 2, 3)
    ]
    doc = DocumentResult(metadata=_metadata(page_count=3), pages=pages)
    markdown = to_markdown(doc)
    for page_number in (1, 2, 3):
        for label in ("a", "b"):
            assert markdown.count(f"page-{page_number}-block-{label}") == 1


def _node(
    node_id: str,
    content: str,
    *,
    kind: ChunkKind = ChunkKind.PARAGRAPH,
    metadata: dict[str, str] | None = None,
    children: list[StructuredChunk] | None = None,
    page: int = 1,
    order: int = 0,
) -> StructuredChunk:
    return StructuredChunk(
        id=node_id,
        kind=kind,
        content=content,
        page_number=page,
        reading_order=order,
        metadata={} if metadata is None else metadata,
        children=[] if children is None else children,
    )


def _chunk(kind: ChunkKind, content: str, **metadata: str) -> StructuredChunk:
    return StructuredChunk(
        id=f"c-{kind}",
        kind=kind,
        content=content,
        page_number=1,
        reading_order=0,
        metadata=metadata,
    )


def _document(*chunks: StructuredChunk) -> DocumentResult:
    page = PageResult(page_number=1, blocks=list(chunks))
    return DocumentResult(metadata=_metadata(page_count=1), pages=[page])


def test_multipage_projection_keeps_page_order() -> None:
    doc = DocumentResult(
        metadata=_metadata(page_count=3),
        pages=[PageResult(page_number=n) for n in (1, 2, 3)],
    )
    markdown = to_markdown(doc)
    positions = [markdown.index(f"<!-- page {n} -->") for n in (1, 2, 3)]
    assert positions == sorted(positions)


def _metadata(page_count: int) -> DocumentMetadata:
    return DocumentMetadata(
        source_uri="file:///sample.pdf",
        source_hash=_SHA,
        format="pdf",
        page_count=page_count,
        produced_at=datetime(2026, 1, 1, tzinfo=UTC),
        package_version="0.0.0",
    )
