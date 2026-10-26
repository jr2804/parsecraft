"""Content-stage normalization for the native MinerU2.5 protocol — pure Python.

Deterministic transforms on recognized block content: OTSL tables to HTML,
markdown tables to HTML, tagged image-analysis output to fields, and equation
bracket wrapping. Ported from ``mineru-vl-utils`` ``post_process`` (Apache-2.0,
MinerU Team; see ADR-0007) minus its model-specific micro-fixers, which are
deliberately not ported — a wrong fix is worse than an honest raw string.
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

#: A markdown table separator row, e.g. ``| --- | :---: |``.
_SEPARATOR_PATTERN = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")

#: Tagged fields of the image-analysis output (``<|class_start|>…<|class_end|>``).
_TAGGED_FIELD_PATTERN = re.compile(r"<\|(\w+)_start\|>(.*?)<\|\1_end\|>", re.DOTALL)

#: The extraction model's explicit "nothing here" answer.
_NON_TEXT = "[Non-Text]"

#: Fields recognized in image-analysis output.
_IMAGE_ANALYSIS_FIELDS = frozenset({"class", "sub_class", "caption", "content"})


def otsl_to_html(content: str) -> str:
    """Convert an OTSL table to HTML; content that already starts as a table
    passes through unchanged (the model sometimes emits HTML directly).
    """
    text = content.strip()
    if text.startswith("<table"):
        return text
    tokens = _otsl_tokenize(text)
    rows = _otsl_rows(tokens)
    return _otsl_html(rows)


#: Cell-slot tokens: each opens one grid cell (span continuations become
#: empty slots — the grid shape survives, spans stay implicit).
_OTSL_CELL_TOKENS = frozenset({"<fcel>", "<ecel>", "<lcel>", "<ucel>", "<xcel>"})

#: Split keeping the structural tokens interleaved with the cell texts.
_OTSL_SPLIT = re.compile(r"(<nl>|<fcel>|<ecel>|<lcel>|<ucel>|<xcel>)")

_OTSL_NL = "<nl>"


def _otsl_tokenize(text: str) -> list[str]:
    """Split OTSL into structural tokens and cell text, in order."""
    return [part for part in _OTSL_SPLIT.split(text) if part.strip()]


def _otsl_rows(tokens: list[str]) -> list[list[str]]:
    """Group a token stream into rows of cell texts, padded to equal width."""
    rows: list[list[str]] = []
    row: list[str] = []
    cell: str | None = None

    def _close_cell() -> None:
        nonlocal cell
        if cell is not None:
            row.append(cell)
            cell = None

    for token in tokens:
        if token in _OTSL_CELL_TOKENS:
            _close_cell()
            cell = ""
        elif token == _OTSL_NL:
            _close_cell()
            rows.append(row)
            row = []
        else:
            cell = (cell or "") + token
    _close_cell()
    if row:
        rows.append(row)
    width = max((len(row) for row in rows), default=0)
    return [row + [""] * (width - len(row)) for row in rows]


def _otsl_html(rows: list[list[str]]) -> str:
    """Render rows as a plain ``<table>`` (no span computation: the flattened
    projection is what consumers read, and spans carry no extra text).
    """
    if not rows:
        return ""
    body = "".join("<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table>{body}</table>"


def is_separator_line(line: str) -> bool:
    """True when a markdown table row is the ``---`` separator under a header."""
    return _SEPARATOR_PATTERN.match(line) is not None


def markdown_table_to_html(content: str) -> str | None:
    r"""Convert a markdown pipe table to HTML, or ``None`` when not one.

    Escaped pipes (``\|``) are cell content, never separators.
    """
    lines = [line for line in content.strip().splitlines() if line.strip()]
    if len(lines) < 2 or not is_separator_line(lines[1]):
        return None
    header = _split_cells(lines[0])
    body_rows = [_split_cells(line) for line in lines[2:]]
    if not all(all(row) for row in [header, *body_rows]):
        return None  # an all-empty row means this was never a table
    rows_html = "<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in header) + "</tr>"
    rows_html += "".join("<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>" for row in body_rows)
    return f"<table>{rows_html}</table>"


def _split_cells(line: str) -> list[str]:
    """Split a markdown row on unescaped pipes; border pipes are trimmed."""
    stripped = line.strip().removeprefix("|").removesuffix("|")
    cells: list[str] = []
    cell: list[str] = []
    escaped = False
    for char in stripped:
        if escaped:
            cell.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(cell).strip())
            cell = []
        else:
            cell.append(char)
    cells.append("".join(cell).strip())
    return cells


def parse_image_analysis(content: str) -> dict[str, str]:
    """Extract the tagged fields of image-analysis output (class, sub_class,
    caption, content). An unclosed ``content`` tag with exactly one opener
    takes everything after it (observed model behavior upstream).
    """
    fields = {name: value.strip() for name, value in _TAGGED_FIELD_PATTERN.findall(content) if name in _IMAGE_ANALYSIS_FIELDS}
    if "content" not in fields and content.count("<|content_start|>") == 1:
        tail = content.split("<|content_start|>", 1)[1]
        if "<|content_end|>" not in tail:
            fields["content"] = tail.strip()
    return fields


def wrap_equation(content: str) -> str:
    r"""Wrap an equation in display-math ``\[ \]`` brackets unless it already
    carries them.
    """
    text = content.strip()
    if text.startswith("\\[") and text.endswith("\\]"):
        return text
    return f"\\[ {text} \\]"


def is_non_text(content: str | None) -> bool:
    """True when a block's extraction came back empty or explicitly non-text."""
    return content is None or content.strip() in ("", _NON_TEXT)


class TableTextExtractor(HTMLParser):
    """Flatten table HTML to ``cell | cell`` rows — the IR table projection."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(cell for cell in self._row):
                self._rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def render(self) -> str:
        """The flattened rows, one ``cell | cell`` line per table row."""
        return "\n".join(" | ".join(row) for row in self._rows)

    def rows(self) -> tuple[tuple[str, ...], ...]:
        """The flattened rows as structured table data."""
        return tuple(tuple(row) for row in self._rows)
