"""Markdown input adapter: parse `.md` text into the canonical IR.

INPUT parsing only — this module never reads rendered Markdown output of
:mod:`parsecraft.ir.markdown`; the IR stays the single source of truth.
Deterministic: same input text (and fixed ``produced_at``) yields an
identical ``DocumentResult``.
"""

from __future__ import annotations

import hashlib
import re
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

#: YAML-fence marker for frontmatter detection (exact line, surrounding
#: whitespace tolerated). Opens the block; closes it unless YAML's ``...``
#: end marker does (see ``_FRONTMATTER_END``).
_FRONTMATTER_FENCE = "---"

#: YAML's end-of-document marker — accepted as a closing fence. Legal YAML
#: and present in real files; without it the closer would be missed.
_FRONTMATTER_END = "..."

#: One top-level ``key:`` line — the minimum evidence the fenced block is data
#: rather than a thematic break. Starts at column zero (no leading space:
#: indented lines are nested YAML or prose and must not trigger detection),
#: carries a colon; ``re``-level detection only, never YAML parsing. A
#: non-blank value always counts; a blank value counts only when the next
#: line continues the block mapping (see ``_continues_block_mapping``),
#: because bare ``Some Title:`` prose also ends in a colon.
_FRONTMATTER_KEY = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*[ \t]*:")

#: Line-break pattern mirroring markdown-it's own newline normalization
#: (``NEWLINES_RE = \\r\\n?|\\n``). Both the offset table and the
#: frontmatter detector must split lines exactly the way the tokenizer
#: does; anything narrower undercounts CR-only lines and token maps
#: outrun the table.
_LINE_BREAK = re.compile(r"\r\n?|\n")


class _ChunkBuilder:
    """Turns a flat markdown-it token stream into ordered ``StructuredChunk``s."""

    def __init__(self, text: str, offsets: list[int], *, start_order: int = 0, line_base: int = 0) -> None:
        self._text = text
        self._offsets = offsets
        self._order = start_order
        self._line_base = line_base
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
        # ``line_base`` shifts token maps (relative to the fenced-off rest)
        # back onto the original document, so spans stay exact character
        # offsets into the caller's text.
        start_line = token.map[0] + self._line_base
        end_line = token.map[1] + self._line_base
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
        # One chunk PER LIST ITEM: merging siblings into a single blob loses
        # item boundaries a consumer cannot recover (pc-3vo). Continuation
        # lines stay with their item; nested lists stay attached (flattened
        # into the parent item's content, as before) rather than re-nested —
        # sibling separation is the data-loss fix, and keeping nesting shape
        # byte-identical avoids surprising existing consumers of nested lists.
        for item_index, (item_open, item_close) in enumerate(_item_ranges(tokens, index, close)):
            self._add(
                ChunkKind.LIST,
                self._container_content(tokens, item_open, item_close),
                tokens[item_open],
                {"list_type": list_type, "item_index": str(item_index)},
            )
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
    # Line breaks counted exactly the way markdown-it counts them: its
    # ``normalize`` core rule folds ``\\r\\n?`` into ``\\n`` before
    # tokenising, so an offset table built on ``split("\\n")`` undercounts
    # every lone-CR line and token maps outrun the table (IndexError in
    # ``_span``). ``finditer`` end positions keep offsets in ORIGINAL
    # coordinates — the caller's string itself is never rewritten, so
    # ``source_span`` still slices the original characters
    # character-for-character, CRLF included (offsets index Python ``str``
    # positions, not UTF-8 byte offsets).
    offsets = [0]
    for match in _LINE_BREAK.finditer(text):
        offsets.append(match.end())
    tail = text[offsets[-1] :]
    offsets.append(offsets[-1] + len(tail) + 1)
    frontmatter, rest, skipped_lines = _split_frontmatter(text)
    builder = _ChunkBuilder(
        text,
        offsets,
        start_order=1 if frontmatter is not None else 0,
        line_base=skipped_lines,
    )
    blocks = builder.build(MarkdownIt("commonmark").enable("table").parse(rest))
    if frontmatter is not None:
        # Source span of the fence block: `offsets[skipped_lines]` is where
        # the first post-fence line starts, so step back over the linebreak
        # terminating the closing fence (CRLF-aware — spans are source
        # offsets, like every other chunk). No rest means the fence ends
        # at EOF with no terminator to step over.
        if rest:
            close_end = offsets[skipped_lines]
            fence_end = close_end - (2 if text[close_end - 2 : close_end] == "\r\n" else 1)
        else:
            fence_end = len(text)
        head = StructuredChunk(
            id="md-0000-unknown",
            kind=ChunkKind.UNKNOWN,
            content=frontmatter,
            page_number=1,
            reading_order=0,
            source_span=SourceSpan(start=0, end=fence_end),
            metadata={"frontmatter": "true"},
        )
        blocks.insert(0, head)
    return blocks, builder.diagnostics


def _split_frontmatter(text: str) -> tuple[str | None, str, int]:
    r"""Split a leading YAML-style frontmatter fence off ``text``.

    Returns ``(raw, rest, skipped_lines)``: the verbatim fenced block, the
    remaining text, and how many leading lines were fenced off. Returns
    ``(None, text, 0)`` when the shape is absent. Detection only, never YAML
    parsing: the first line must be ``---`` (a leading BOM is tolerated for
    the check only — the chunk keeps the original bytes), a later line must
    close with ``---`` or YAML's ``...`` end marker, and at least one line
    between must look like a top-level ``key:`` entry. Anything else —
    including a genuine leading thematic break — tokenises normally.

    The block is kept VERBATIM as one ``UNKNOWN`` chunk (see ``_project``)
    rather than stripped or parsed, and that is deliberate: stripping content
    is a content decision outside a parser's remit (pc-oir design item 6,
    tolerance not repair). A consumer can ignore a chunk carrying
    ``{"frontmatter": "true"}`` metadata but cannot recover YAML that was
    silently deleted — unknown kind plus explicit metadata is reversible,
    deletion is not. Two things stay straight here: the detector returns
    LF-joined slices — ``raw`` content and ``rest`` for the tokenizer,
    byte-identical to what the old code produced — while the caller's
    original ``text`` is never modified and the offset table indexes into
    those original character coordinates. The detector splits on the same
    ``\r\n?|\n`` breaks the tokenizer counts, so a stray ``\r`` cannot
    survive in a detector line by construction — the old explicit CRLF→LF
    scrub and fence-``\r`` trim are subsumed by the split itself.
    A leading BOM is stripped so `content.startswith("---")` holds; that
    strip is reversible per pc-oir design item 6.
    """
    lines = _LINE_BREAK.split(text)
    # BOM (`\ufeff`) is not whitespace to `str.strip()`: without the explicit
    # `lstrip` a BOM-prefixed document would silently skip frontmatter. The
    # check tolerates it; the chunk below keeps the original bytes verbatim.
    if not lines or lines[0].lstrip("\ufeff").strip() != _FRONTMATTER_FENCE:
        return None, text, 0
    for end in range(1, len(lines)):
        if lines[end].strip() in (_FRONTMATTER_FENCE, _FRONTMATTER_END):
            if _has_frontmatter_key(lines, end):
                raw = "\n".join(lines[: end + 1]).removeprefix("\ufeff")
                return raw, "\n".join(lines[end + 1 :]), end + 1
            return None, text, 0
    return None, text, 0


def _has_frontmatter_key(lines: list[str], end: int) -> bool:
    """Whether lines ``[1, end)`` hold at least one top-level ``key:`` entry."""
    for i in range(1, end):
        match = _FRONTMATTER_KEY.match(lines[i])
        if match is None:
            continue
        if lines[i][match.end() :].strip():
            return True  # `key: value` — a non-blank value always counts.
        # `key:` with a blank value counts only when the next line continues
        # the block mapping — the `tags:` + `- item` shape real frontmatter
        # uses. `i + 1` stays in range: `i < end < len(lines)` by construction.
        if _continues_block_mapping(lines[i + 1]):
            return True
    return False


def _continues_block_mapping(next_line: str) -> bool:
    """Whether ``next_line`` continues a blank-valued key's block mapping."""
    stripped = next_line.strip()
    return bool(stripped) and (next_line[0] in " \t" or stripped == "-" or stripped.startswith("- "))


def _item_ranges(tokens: list[Token], index: int, close: int) -> list[tuple[int, int]]:
    """Top-level ``list_item_open → list_item_close`` ranges of one list.

    Depth-tracked: only an item opener seen at depth zero starts a segment,
    so a nested list's items stay inside their parent item's range. markdown-it
    always emits balanced item pairs, so every close at depth zero has a
    preceding open and ``start`` is always assigned before use.
    """
    segments: list[tuple[int, int]] = []
    start = -1
    depth = 0
    for probe in range(index + 1, close):
        token = tokens[probe]
        if token.type == "list_item_open" and depth == 0:
            start = probe
        depth += token.nesting
        if token.type == "list_item_close" and depth == 0:
            segments.append((start, probe))
    return segments


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
