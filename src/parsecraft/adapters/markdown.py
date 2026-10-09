"""Markdown input adapter: parse `.md` text into the canonical IR.

INPUT parsing only — this module never reads rendered Markdown output of
:mod:`parsecraft.ir.markdown`; the IR stays the single source of truth.
Deterministic: same input text (and fixed ``produced_at``) yields an
identical ``DocumentResult``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime

from markdown_it import MarkdownIt
from markdown_it.token import Token

from parsecraft.ir.models import (
    ChunkKind,
    Diagnostic,
    DiagnosticLevel,
    DocumentMetadata,
    DocumentResult,
    PageResult,
    SourceSpan,
    StructuredChunk,
)

_TEXTUAL_INLINE_TYPES = {"text", "code_inline", "html_inline"}


class _ChunkBuilder:
    """Turns a flat markdown-it token stream into ordered ``StructuredChunk``s."""

    def __init__(self, text: str, offsets: list[int]) -> None:
        self._text = text
        self._offsets = offsets
        self._order = 0
        self._chunks: list[StructuredChunk] = []
        self.diagnostics: list[Diagnostic] = []

    def build(self, tokens: list[Token]) -> list[StructuredChunk]:
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.level != 0:
                index += 1
                continue
            handler = getattr(self, f"_handle_{token.type}", None)
            if handler is None:
                self._handle_unknown(token)
                index += 1
                continue
            index += handler(tokens, index)
        return self._chunks

    # ── shared helpers ─────────────────────────────────────────────────────

    def _span(self, token: Token) -> SourceSpan | None:
        if token.map is None:
            return None
        start_line, end_line = token.map
        start = self._offsets[start_line]
        end = self._offsets[end_line] if end_line < len(self._offsets) else len(self._text)
        return SourceSpan(start=start, end=max(end, start))

    def _add(
        self,
        kind: ChunkKind,
        content: str,
        token: Token,
        metadata: dict[str, str] | None = None,
        rows: Sequence[Sequence[str]] | None = None,
    ) -> None:
        self._chunks.append(
            StructuredChunk(
                id=f"md-{self._order:04d}-{kind.value}",
                kind=kind,
                content=content,
                page_number=1,
                reading_order=self._order,
                source_span=self._span(token),
                metadata=metadata or {},
                rows=tuple(tuple(cell for cell in row) for row in rows) if rows is not None else None,
            )
        )
        self._order += 1

    # ── per-type handlers (return number of tokens consumed) ──────────────

    def _handle_heading_open(self, tokens: list[Token], index: int) -> int:
        token = tokens[index]
        inline = tokens[index + 1] if index + 1 < len(tokens) and tokens[index + 1].type == "inline" else None
        text, _ = _inline_text(inline)
        self._add(ChunkKind.HEADING, text, token, {"heading_level": token.tag.removeprefix("h")})
        return 3 if inline is not None else 1

    def _handle_paragraph_open(self, tokens: list[Token], index: int) -> int:
        token = tokens[index]
        inline = tokens[index + 1] if index + 1 < len(tokens) and tokens[index + 1].type == "inline" else None
        text, images = _inline_text(inline)
        stripped = text.strip()
        if stripped.startswith("$$") and stripped.endswith("$$") and len(stripped) > 4:
            self._add(ChunkKind.FORMULA, stripped[2:-2].strip(), token)
            return 3
        if images and not stripped:
            self._add_figure(token, images[0])
            return 3
        if images and stripped:
            self._add_figure(token, images[0])
            self._add(ChunkKind.CAPTION, stripped, token)
            return 3
        self._add(ChunkKind.PARAGRAPH, stripped, token)
        return 3

    def _add_figure(self, token: Token, image: Token) -> None:
        src, alt = _image_parts(image)
        self._add(
            ChunkKind.FIGURE,
            f"![{alt}]({src})",
            token,
            {"image_src": src, "image_alt": alt},
        )

    def _handle_fence(self, tokens: list[Token], index: int) -> int:
        token = tokens[index]
        language = token.info.strip()
        self._add(ChunkKind.CODE, token.content.rstrip("\n"), token, {"language": language} if language else None)
        return 1

    def _handle_code_block(self, tokens: list[Token], index: int) -> int:
        token = tokens[index]
        self._add(ChunkKind.CODE, token.content.rstrip("\n"), token)
        return 1

    def _handle_html_block(self, tokens: list[Token], index: int) -> int:
        token = tokens[index]
        self._add(ChunkKind.UNKNOWN, token.content.strip(), token)
        return 1

    def _handle_inline(self, tokens: list[Token], index: int) -> int:
        text, _ = _inline_text(tokens[index])
        self._add(ChunkKind.PARAGRAPH, text.strip(), tokens[index])
        return 1

    def _handle_bullet_list_open(self, tokens: list[Token], index: int) -> int:
        return self._handle_list(tokens, index, "bullet")

    def _handle_ordered_list_open(self, tokens: list[Token], index: int) -> int:
        return self._handle_list(tokens, index, "ordered")

    def _handle_list(self, tokens: list[Token], index: int, list_type: str) -> int:
        close = self._matching_close(tokens, index)
        self._add(ChunkKind.LIST, self._container_content(tokens, index, close), tokens[index], {"list_type": list_type})
        return close - index + 1

    def _handle_blockquote_open(self, tokens: list[Token], index: int) -> int:
        close = self._matching_close(tokens, index)
        self._add(ChunkKind.QUOTE, self._container_content(tokens, index, close), tokens[index])
        return close - index + 1

    def _handle_table_open(self, tokens: list[Token], index: int) -> int:
        close = self._matching_close(tokens, index)
        rows: list[list[str]] = []
        current_row: list[str] | None = None
        in_cell = False
        for token in tokens[index + 1 : close]:
            if token.type == "tr_open":
                if current_row is not None:
                    rows.append(current_row)
                current_row = []
            elif token.type in {"th_open", "td_open"}:
                in_cell = True
            elif token.type in {"th_close", "td_close"}:
                in_cell = False
            elif token.type == "inline" and in_cell and current_row is not None:
                text, _ = _inline_text(token)
                current_row.append(text)
        if current_row is not None:
            rows.append(current_row)
        lines = [f"| {' | '.join(row)} |" for row in rows]
        if lines:
            lines.insert(1, "| " + " | ".join(["---"] * len(rows[0])) + " |")
        self._add(ChunkKind.TABLE, "\n".join(lines), tokens[index], rows=rows)
        return close - index + 1

    def _handle_unknown(self, token: Token) -> None:
        content = token.content.strip() or (token.markup.strip() if token.markup else "")
        if content:
            self._add(ChunkKind.UNKNOWN, content, token)
            return
        self.diagnostics.append(
            Diagnostic(
                level=DiagnosticLevel.INFO,
                code="markdown-empty-token",
                message=f"token {token.type!r} has no content; skipped",
            )
        )

    @staticmethod
    def _matching_close(tokens: list[Token], index: int) -> int:
        depth = tokens[index].nesting
        for probe in range(index + 1, len(tokens)):
            depth += tokens[probe].nesting
            if depth == 0:
                return probe
        return len(tokens) - 1

    @staticmethod
    def _container_content(tokens: list[Token], index: int, close: int) -> str:
        parts: list[str] = []
        for token in tokens[index + 1 : close]:
            if token.type == "inline":
                text, _ = _inline_text(token)
                if text:
                    parts.append(text)
        return "\n".join(parts)


def markdown_blocks(text: str) -> list[StructuredChunk]:
    """Project Markdown text into ordered typed chunks.

    Output-side twin of :func:`parse_markdown`: the same token stream and the
    same chunk kinds, but no document metadata — a backend that *renders*
    Markdown (rather than parsing a Markdown source) projects its output
    through this so headings, lists, tables and code fences land in the IR as
    typed chunks instead of one undifferentiated paragraph.
    """
    blocks, _ = _project(text)
    return blocks


def parse_markdown(
    source: str | bytes,
    source_uri: str,
    *,
    title: str | None = None,
    package_version: str = "0.0.0",
    produced_at: datetime | None = None,
) -> DocumentResult:
    """Parse a Markdown document (text or UTF-8 bytes) into the canonical IR."""
    text = source.decode("utf-8") if isinstance(source, bytes) else source

    produced = produced_at if produced_at is not None else datetime.now(UTC)
    blocks, diagnostics = _project(text)

    metadata = DocumentMetadata(
        source_uri=source_uri,
        source_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        format="text/markdown",
        page_count=1,
        title=title,
        produced_at=produced,
        package_version=package_version,
    )
    return DocumentResult(
        metadata=metadata,
        pages=[PageResult(page_number=1, blocks=blocks, diagnostics=diagnostics)],
    )


def _project(text: str) -> tuple[list[StructuredChunk], list[Diagnostic]]:
    """Shared core: tokenise ``text`` and build the ordered chunk projection."""
    offsets = [0]
    for line in text.split("\n"):
        offsets.append(offsets[-1] + len(line) + 1)
    builder = _ChunkBuilder(text, offsets)
    blocks = builder.build(MarkdownIt("commonmark").enable("table").parse(text))
    return blocks, builder.diagnostics


def _inline_text(inline: Token | None) -> tuple[str, list[Token]]:
    """Plain text of an inline token plus its image children."""
    if inline is None or inline.children is None:
        return "", []
    parts: list[str] = []
    images: list[Token] = []
    for child in inline.children:
        if child.type in _TEXTUAL_INLINE_TYPES:
            parts.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            parts.append("\n")
        elif child.type == "image":
            images.append(child)
    return "".join(parts), images


def _image_parts(image: Token) -> tuple[str, str]:
    """(src, alt) of an image token."""
    attrs = image.attrs or {}
    return str(attrs.get("src", "")), image.content or ""
