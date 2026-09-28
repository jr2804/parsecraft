"""HTML native backend — stdlib ``html.parser``, no external dependencies.

Extracts document structure only: headings, paragraphs, lists, tables, code
blocks, and quotes. ``<img>`` alt text and ``<script>``/``<style>`` content
are not document text — images are counted for analysis, skipped markup is
recorded as INFO diagnostics.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser

from parsecraft.backends.native._common import NATIVE_BACKEND_VERSION, NativeBackendBase, one_page
from parsecraft.backends.protocol import (
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    BackendFactory,
    DocumentBackend,
    SourceDocument,
)
from parsecraft.ir.models import (
    ChunkKind,
    Diagnostic,
    DiagnosticLevel,
    PageResult,
    StructuredChunk,
)

BACKEND_NAME = "native-html"
SUPPORTED_FORMATS = ["text/html"]

_SKIP_TAGS = frozenset({"script", "style", "head", "title"})
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_LIST_TAGS = frozenset({"ul", "ol"})
#: Block starts that implicitly close an open paragraph/heading (HTML auto-close).
_AUTO_CLOSE_TAGS = _HEADING_TAGS | _LIST_TAGS | frozenset({"p", "blockquote", "pre", "table"})
_CELL_TAGS = frozenset({"th", "td"})


@dataclass
class _ListContext:
    """One open list: its tag, completed items, and the in-flight item."""

    tag: str
    items: list[str] = field(default_factory=list)
    item_buf: list[str] | None = None


class _HtmlChunkParser(HTMLParser):
    """Streams HTML into ordered, typed chunks (single pass, deterministic)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.diagnostics: list[Diagnostic] = []
        self._chunks: list[StructuredChunk] = []
        self._order = 0
        self._skip: str | None = None
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._lists: list[_ListContext] = []
        self._code: list[str] | None = None
        self._code_lang = ""
        self._kind: str | None = None
        self._heading_tag = ""
        self._buf: list[str] = []
        self._stray: list[str] = []

    def finish(self) -> tuple[list[StructuredChunk], list[Diagnostic]]:
        """Flush all open constructs after ``close()`` and return the results."""
        self.close()
        self._flush_stray()
        if self._kind is not None:
            self._flush_text()
        if self._code is not None:
            self._flush_code()
        while self._lists:
            self._close_list(self._lists.pop())
        if self._table is not None:
            self._finalize_row()
            self._flush_table()
        return self._chunks, self.diagnostics

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip is not None:
            pass
        elif tag in _AUTO_CLOSE_TAGS and self._kind in ("paragraph", "heading"):
            self._flush_text()
            self.handle_starttag(tag, attrs)
        elif tag in _SKIP_TAGS:
            self._skip = tag
        elif tag == "table":
            if self._table is None:
                self._table = []
                self._row = None
                self._cell = None
        elif self._table is not None:
            if tag == "tr":
                self._row = []
            elif tag in _CELL_TAGS and self._row is not None:
                self._cell = []
        elif tag in _LIST_TAGS:
            self._lists.append(_ListContext(tag))
        elif tag == "li":
            if self._lists:
                context = self._lists[-1]
                if context.item_buf is not None:
                    context.items.append("".join(context.item_buf).strip())
                    context.item_buf = []
                context.item_buf = []
        elif tag == "pre":
            if not self._in_item() and self._code is None:
                self._code = []
                self._code_lang = _class_language(attrs)
        elif tag == "code":
            if self._code is not None and not self._code_lang:
                self._code_lang = _class_language(attrs)
        elif tag in _HEADING_TAGS:
            if self._begin_text("heading"):
                self._heading_tag = tag
        elif tag == "p":
            self._begin_text("paragraph")
        elif tag == "blockquote":
            self._begin_text("quote")

    def handle_endtag(self, tag: str) -> None:
        if self._skip is not None:
            if tag == self._skip:
                self._skip = None
        elif tag in _HEADING_TAGS:
            if self._kind == "heading" and tag == self._heading_tag:
                self._flush_text()
        elif tag == "p":
            if self._kind == "paragraph":
                self._flush_text()
        elif tag == "blockquote":
            if self._kind == "quote":
                self._flush_text()
        elif tag in _LIST_TAGS:
            if self._lists and self._lists[-1].tag == tag:
                self._close_list(self._lists.pop())
        elif tag == "li":
            if self._lists:
                context = self._lists[-1]
                if context.item_buf is not None:
                    context.items.append("".join(context.item_buf).strip())
                    context.item_buf = None
        elif tag == "pre":
            if self._code is not None:
                self._flush_code()
        elif tag in _CELL_TAGS:
            if self._cell is not None and self._row is not None:
                self._row.append("".join(self._cell).strip())
                self._cell = None
        elif tag == "tr":
            self._finalize_row()
        elif tag == "table" and self._table is not None:
            self._finalize_row()
            self._flush_table()

    def handle_data(self, data: str) -> None:
        if self._skip is not None:
            if data.strip():
                self.diagnostics.append(
                    Diagnostic(
                        level=DiagnosticLevel.INFO,
                        code="html-skipped-content",
                        message=f"content inside <{self._skip}> is not document text; ignored",
                    )
                )
            return
        if self._table is not None:
            if self._cell is not None:
                self._cell.append(data)
            return
        if self._lists and self._lists[-1].item_buf is not None:
            self._lists[-1].item_buf.append(data)
            return
        if self._code is not None:
            self._code.append(data)
            return
        if self._kind is not None:
            self._buf.append(data)
            return
        self._stray.append(data)

    # ── internal state helpers ─────────────────────────────────────────────

    def _in_item(self) -> bool:
        return bool(self._lists and self._lists[-1].item_buf is not None)

    def _finalize_row(self) -> None:
        """Fold any open cell and row into the open table (close/EOF)."""
        if self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        if self._row is not None and self._table is not None:
            self._table.append(self._row)
            self._row = None

    def _begin_text(self, kind: str) -> bool:
        if self._kind is not None or self._code is not None or self._table is not None or self._in_item():
            return False
        self._kind = kind
        self._buf = []
        return True

    def _flush_stray(self) -> None:
        if not self._stray:
            return
        text = "".join(self._stray)
        self._stray = []
        if text.strip():
            self._emit(ChunkKind.PARAGRAPH, text.strip())

    def _flush_text(self) -> None:
        content = "".join(self._buf).strip()
        kind_tag = self._kind
        heading_tag = self._heading_tag
        self._kind = None
        self._heading_tag = ""
        self._buf = []
        if kind_tag == "heading":
            self._emit(ChunkKind.HEADING, content, {"heading_level": heading_tag.removeprefix("h")})
        elif kind_tag == "paragraph":
            self._emit(ChunkKind.PARAGRAPH, content)
        elif kind_tag == "quote":
            self._emit(ChunkKind.QUOTE, content)

    def _flush_code(self) -> None:
        content = "".join(self._code or []).strip()
        language = self._code_lang
        self._code = None
        self._code_lang = ""
        self._emit(ChunkKind.CODE, content, {"language": language} if language else None)

    def _close_list(self, context: _ListContext) -> None:
        if context.item_buf is not None:
            context.items.append("".join(context.item_buf).strip())
            context.item_buf = None
        if not context.items:
            self.diagnostics.append(
                Diagnostic(
                    level=DiagnosticLevel.INFO,
                    code="html-empty-list",
                    message="list without items; ignored",
                )
            )
            return
        list_type = "bullet" if context.tag == "ul" else "ordered"
        self._emit(ChunkKind.LIST, "\n".join(context.items), {"list_type": list_type})

    def _flush_table(self) -> None:
        table = self._table or []
        self._table = None
        self._row = None
        self._cell = None
        if not table:
            self.diagnostics.append(
                Diagnostic(
                    level=DiagnosticLevel.INFO,
                    code="html-empty-table",
                    message="table without rows; ignored",
                )
            )
            return
        lines = [f"| {' | '.join(row)} |" for row in table]
        lines.insert(1, f"| {' | '.join(['---'] * len(table[0]))} |")
        self._emit(ChunkKind.TABLE, "\n".join(lines), rows=table)

    def _emit(
        self,
        kind: ChunkKind,
        content: str,
        metadata: dict[str, str] | None = None,
        rows: Sequence[Sequence[str]] | None = None,
    ) -> None:
        self._flush_stray()
        if not content:
            self.diagnostics.append(
                Diagnostic(
                    level=DiagnosticLevel.INFO,
                    code="html-empty-block",
                    message=f"empty {kind.value} block; ignored",
                )
            )
            return
        self._chunks.append(
            StructuredChunk(
                id=f"native-html-{self._order:04d}-{kind.value}",
                kind=kind,
                content=content,
                page_number=1,
                reading_order=self._order,
                metadata=metadata or {},
                rows=tuple(tuple(cell for cell in row) for row in rows) if rows is not None else None,
            )
        )
        self._order += 1


class HtmlBackend(NativeBackendBase):
    """HTML document parsed with the stdlib streaming parser."""

    name = BACKEND_NAME

    def __init__(self, config: BackendConfig, capabilities: BackendCapabilities) -> None:
        super().__init__(
            config,
            capabilities,
            extract=extract_html_pages,
            page_count=one_page,
            count_images=count_html_images,
        )


class _HtmlFactory:
    """Light entry-point object: descriptor + build."""

    descriptor = BackendDescriptor(
        name=BACKEND_NAME,
        version=NATIVE_BACKEND_VERSION,
        capabilities=BackendCapabilities(
            supported_formats=SUPPORTED_FORMATS,
            supports_page_ranges=False,
            supports_multi_page=False,
        ),
    )

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        return HtmlBackend(config, self.descriptor.capabilities)


def _class_language(attrs: list[tuple[str, str | None]]) -> str:
    """``language-<name>`` token from a ``class`` attribute, if any."""
    for key, value in attrs:
        if key == "class" and value:
            for token in value.split():
                if token.startswith("language-"):
                    return token.removeprefix("language-")
    return ""


def count_html_images(data: bytes) -> int:
    """Deterministic image heuristic: count ``<img`` tags (case-insensitive)."""
    return data.lower().count(b"<img")


def extract_html_pages(source: SourceDocument, data: bytes) -> list[PageResult]:
    """Parse HTML bytes into one page of typed chunks."""
    parser = _HtmlChunkParser()
    parser.feed(data.decode("utf-8", errors="replace"))
    chunks, diagnostics = parser.finish()
    return [PageResult(page_number=1, blocks=chunks, diagnostics=diagnostics)]


factory: BackendFactory = _HtmlFactory()
