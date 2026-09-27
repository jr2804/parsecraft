"""Deterministic Markdown projection of the canonical IR.

The renderer is in-house and dependency-free: its byte output *is* the
contract consumers diff and test against. Every block appears exactly once —
no silent dropping. Rules: ``AGENTS.md`` next to this file.
"""

from __future__ import annotations

from collections.abc import Callable

from parsecraft.ir.models import ChunkKind, DocumentResult, PageResult, StructuredChunk

#: Fallback heading level when a heading chunk carries no ``heading_level``.
DEFAULT_HEADING_LEVEL = 2

_LEVEL_KEY = "heading_level"
_LANGUAGE_KEY = "language"


def _heading(chunk: StructuredChunk) -> str:
    raw = chunk.metadata.get(_LEVEL_KEY)
    level = DEFAULT_HEADING_LEVEL
    if raw is not None and raw.isdigit() and 1 <= int(raw) <= 6:
        level = int(raw)
    return f"{'#' * level} {chunk.content}"


def _code(chunk: StructuredChunk) -> str:
    language = chunk.metadata.get(_LANGUAGE_KEY, "")
    return f"```{language}\n{chunk.content}\n```"


def _formula(chunk: StructuredChunk) -> str:
    return f"$$\n{chunk.content}\n$$"


def _quote(chunk: StructuredChunk) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in chunk.content.splitlines())


def _header(chunk: StructuredChunk) -> str:
    return _comment("header", chunk.content)


def _footer(chunk: StructuredChunk) -> str:
    return _comment("footer", chunk.content)


def _comment(label: str, content: str) -> str:
    # Keep block content from terminating the HTML comment early.
    safe = content.replace("-->", "-- >")
    return f"<!-- {label}: {safe} -->"


_BY_KIND: dict[ChunkKind, Callable[[StructuredChunk], str]] = {
    ChunkKind.HEADING: _heading,
    ChunkKind.CODE: _code,
    ChunkKind.FORMULA: _formula,
    ChunkKind.QUOTE: _quote,
    ChunkKind.HEADER: _header,
    ChunkKind.FOOTER: _footer,
}


def to_markdown(result: DocumentResult) -> str:
    """Project a full document to deterministic Markdown (trailing newline)."""
    if not result.pages:
        return ""
    return "\n\n".join(render_page(page) for page in result.pages) + "\n"


def render_page(page: PageResult) -> str:
    """Render one page: marker comment, then every block in reading order."""
    body = "\n\n".join(render_block(block) for block in page.blocks)
    marker = f"<!-- page {page.page_number} -->"
    return f"{marker}\n\n{body}" if body else marker


def render_block(chunk: StructuredChunk) -> str:
    """Render one chunk and its descendants to Markdown, depth-first in order."""
    parts = [render_chunk(chunk), *(render_block(child) for child in chunk.children)]
    return "\n\n".join(part for part in parts if part)


def render_chunk(chunk: StructuredChunk) -> str:
    """Render a chunk's own content according to its kind (no descendants)."""
    renderer = _BY_KIND.get(chunk.kind)
    if renderer is None:
        return chunk.content
    return renderer(chunk)
