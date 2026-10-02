"""Layout recovery v1: turn positioned text lines into PARAGRAPH and CODE chunks.

The PDF text layer has no paragraph or code structure — only positioned glyphs.
PyMuPDF's page dictionary exposes what we need to recover a *useful* amount of
it: per-line geometry (``y0``/``y1``) and per-span font names and left edges
(``x0``). This module is the whole decision, kept pure and dependency-free
(``pdf_text.py`` only maps PyMuPDF's dictionary into these models), so the rules
below are unit-tested without the AGPL ``pdf`` extra.

What v1 recovers, and what it deliberately does not:

- **Paragraphs** stay what they always were: blank-line-separated blocks. A blank
  line is a paragraph break whether the text layer spells it (an empty line) or
  only positions it (a vertical gap), so prose output is unchanged.
- **CODE** is a run of consecutive *monospace-majority* lines. Monospace is a
  font-name fact, not a heuristic on the text: markers and exclusions are
  explicit lists below (``Monotype`` is the trap — it contains "mono").
- **Indentation** is relative to the block's own leftmost glyph, in estimated
  character columns (``SPACE_WIDTH_RATIO`` × font size). PDF positions glyphs, so
  this is an approximation, not a column reconstruction — chasing real columns
  needs per-glyph advance widths and is explicitly out of v1 scope.
- **Wrapped lines** are joined conservatively: only when the next line is not
  dedented and the previous one ends without a statement terminator. A joined
  line keeps one space where the wrap happened (the POLQA evidence shows the
  first half ending in a space, e.g. ``unsigned long `` + ``mulMode; ``).

Every chunk this module emits is typed IR; nothing here reads files, imports a
PDF library, or looks at the network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, Field

from parsecraft.ir.models import ChunkKind, Diagnostic, DiagnosticLevel, StructuredChunk
from parsecraft.routing.rules import FEATURE_CODE_CODE

#: Diagnostic code advertised when a page's CODE mass is significant. One
#: canonical spelling lives in the routing feature-code vocabulary; this module
#: imports it rather than repeating the literal.
FEATURE_CODE = FEATURE_CODE_CODE

#: Share of a page's emitted characters that must be CODE for the hint to fire.
CODE_MASS_THRESHOLD = 0.10

#: Font-name markers that identify a monospace family, matched case-insensitively
#: as substrings (PDF font names are subset-prefixed, e.g. ``ABCDEF+NimbusMonoPS``).
MONOSPACE_MARKERS: tuple[str, ...] = (
    "courier",
    "consola",  # Consolas / Consola — the five-letter spelling trips the spell checker
    "mono",
    "menlo",
    "monaco",
    "inconsolata",
    "cascadia",
    "sourcecode",
    "fira code",
    "jetbrains",
    "andale",
    "lucidaconsole",
    "code",
)

#: Names that contain a marker but are NOT monospace (``Monotype`` contains
#: "mono"; ``MonotypeCorsiva`` and friends are proportional scripts).
MONOSPACE_EXCLUSIONS: tuple[str, ...] = ("monotype", "monospace-narrow-excluded")

#: Minimum share of a line's characters that must sit in monospace spans for the
#: line to count as code — a prose line quoting one monospace identifier is not.
LINE_CODE_RATIO = 0.6

#: Monospace advance width as a fraction of font size (typical for Courier-like
#: faces). Used only to convert x0 deltas into an estimated column count.
SPACE_WIDTH_RATIO = 0.6

#: Indentation is clamped: a pathological left edge must not emit 200 spaces.
MAX_INDENT_COLUMNS = 40

#: A vertical gap wider than this fraction of the line height starts a new
#: paragraph, standing in for the blank line a text-mode extraction would show.
PARAGRAPH_GAP_RATIO = 0.6

#: A line ending in one of these is complete; the next line is not a wrap.
STATEMENT_TERMINATORS: tuple[str, ...] = (";", "{", "}", ",", ":", "#", "\\")

#: A CODE chunk needs at least this many non-whitespace characters.
MIN_CODE_CHARS = 2

#: ``StructuredChunk.metadata`` key for the fence language (empty = no language).
_LANGUAGE_KEY = "language"


class TextSpan(BaseModel):
    """One positioned run of text with a single font — the unit PyMuPDF reports."""

    font: str
    size: float = Field(gt=0)
    x0: float
    x1: float
    text: str

    model_config = {"frozen": True}


class TextLine(BaseModel):
    """One text line: its vertical extent plus the spans it is made of."""

    y0: float
    y1: float
    spans: tuple[TextSpan, ...]

    model_config = {"frozen": True}

    @property
    def text(self) -> str:
        """The line's text, spans concatenated in reading order."""
        return "".join(span.text for span in self.spans)

    @property
    def x0(self) -> float:
        """Left edge of the line's first span (0.0 for an empty line)."""
        return self.spans[0].x0 if self.spans else 0.0

    @property
    def size(self) -> float:
        """Font size of the line's first span (0.0 for an empty line)."""
        return self.spans[0].size if self.spans else 0.0

    @property
    def height(self) -> float:
        """Vertical extent of the line."""
        return max(self.y1 - self.y0, 0.0)


class PageText(BaseModel):
    """One page's positioned lines, in extraction order."""

    lines: tuple[TextLine, ...]

    model_config = {"frozen": True}


class _SpanDict(BaseModel):
    """One span as PyMuPDF's page dictionary reports it (extras ignored)."""

    font: str = ""
    size: float = 0.0
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    text: str = ""

    model_config = {"extra": "ignore"}


class _LineDict(BaseModel):
    """One line of a PyMuPDF text block."""

    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    spans: tuple[_SpanDict, ...] = ()

    model_config = {"extra": "ignore"}


class _BlockDict(BaseModel):
    """One block of a PyMuPDF page (image blocks carry no lines)."""

    lines: tuple[_LineDict, ...] = ()

    model_config = {"extra": "ignore"}


class _PageDict(BaseModel):
    """The PyMuPDF page dictionary ``get_text("dict")`` returns."""

    blocks: tuple[_BlockDict, ...] = ()

    model_config = {"extra": "ignore"}


class PageLayout(BaseModel):
    """The chunks a page yields, plus how much of its text became CODE."""

    chunks: list[StructuredChunk]
    code_mass: float = Field(ge=0.0, le=1.0)

    model_config = {"frozen": True}

    @property
    def diagnostics(self) -> list[Diagnostic]:
        """The ``feature:code`` hint when CODE mass is significant, else nothing."""
        if self.code_mass < CODE_MASS_THRESHOLD:
            return []
        percent = round(self.code_mass * 100)
        return [
            Diagnostic(
                level=DiagnosticLevel.INFO,
                code=FEATURE_CODE,
                message=f"{percent}% of this page's text is monospace code (fenced in the Markdown projection)",
            )
        ]


def page_text(page_dict: Mapping[str, object]) -> PageText:
    """Map one PyMuPDF page dictionary into typed positioned lines.

    The vendor shape is validated (not duck-typed) and unknown keys are ignored,
    so the mapping is unit-testable from plain dicts — the whole reason this
    lives here rather than in the heavy ``pdf_text`` module. Spans without a
    usable size or text are dropped: they carry no geometry to place a glyph.
    """
    blocks = _PageDict.model_validate(page_dict).blocks
    return PageText(
        lines=tuple(
            TextLine(y0=line.bbox[1], y1=line.bbox[3], spans=tuple(spans))
            for block in blocks
            for line in block.lines
            if (spans := [_span(span) for span in line.spans if span.size > 0 and span.text])
        )
    )


def _span(span: _SpanDict) -> TextSpan:
    return TextSpan(font=span.font, size=span.size, x0=span.bbox[0], x1=span.bbox[2], text=span.text)


def build_chunks(page: PageText, *, page_number: int, prefix: str = "native-pdf") -> PageLayout:
    """Recover a page's PARAGRAPH and CODE chunks from its positioned lines."""
    chunks: list[StructuredChunk] = []
    code_chars = 0
    paragraph: list[str] = []
    code_run: list[TextLine] = []

    def flush_paragraph() -> None:
        text = " ".join(line.strip() for line in paragraph).strip()
        paragraph.clear()
        if text:
            chunks.append(_chunk(prefix, page_number, len(chunks), ChunkKind.PARAGRAPH, text))

    def flush_code() -> None:
        nonlocal code_chars
        lines = _code_lines(code_run)
        code_run.clear()
        if not lines:
            return
        content = "\n".join(lines)
        if len(content.strip()) < MIN_CODE_CHARS:
            return
        code_chars += len(content)
        chunks.append(_chunk(prefix, page_number, len(chunks), ChunkKind.CODE, content))

    previous: TextLine | None = None
    for line in page.lines:
        if _is_code_line(line):
            flush_paragraph()
            code_run.append(line)
        else:
            flush_code()
            if not line.text.strip():
                flush_paragraph()  # an explicit blank line ends the paragraph
            else:
                if _starts_paragraph(previous, line):
                    flush_paragraph()  # the gap spells the blank line the text layer omits
                paragraph.append(line.text)
        previous = line
    flush_code()
    flush_paragraph()

    total = sum(len(chunk.content) for chunk in chunks)
    return PageLayout(chunks=chunks, code_mass=(code_chars / total) if total else 0.0)


def _chunk(prefix: str, page_number: int, order: int, kind: ChunkKind, content: str) -> StructuredChunk:
    return StructuredChunk(
        id=f"{prefix}-{page_number}-b{order}",
        kind=kind,
        content=content,
        page_number=page_number,
        reading_order=order,
        metadata={_LANGUAGE_KEY: ""} if kind is ChunkKind.CODE else {},
    )


def _is_code_line(line: TextLine) -> bool:
    """Whether a line's characters are mostly monospace (a code line, not prose)."""
    total = len(line.text)
    if not total:
        return False
    monospace = sum(len(span.text) for span in line.spans if is_monospace_font(span.font))
    return monospace / total >= LINE_CODE_RATIO


def is_monospace_font(name: str) -> bool:
    """Whether a font name names a monospace family (markers minus exclusions)."""
    lowered = name.lower()
    if any(exclusion in lowered for exclusion in MONOSPACE_EXCLUSIONS):
        return False
    return any(marker in lowered for marker in MONOSPACE_MARKERS)


def _starts_paragraph(previous: TextLine | None, line: TextLine) -> bool:
    """Whether the gap above ``line`` is a paragraph break rather than a line break.

    ``height`` cannot be zero: a line that reaches here carries text, so it has at
    least one span, and ``TextSpan.size`` is validated ``> 0``.
    """
    if previous is None:
        return False
    height = previous.height or line.height or line.size
    return (line.y0 - previous.y1) > PARAGRAPH_GAP_RATIO * height


def _code_lines(lines: Sequence[TextLine]) -> list[str]:
    """Code-block text: relative indentation, then conservative wrapped-line joins."""
    if not lines:
        return []
    base = min(line.x0 for line in lines)
    indented = [(_indent_columns(line.x0 - base, line.size), line.text.strip()) for line in lines]
    joined: list[tuple[int, str]] = []
    for indent, text in indented:
        if joined and _wraps(joined[-1], (indent, text)):
            previous_indent, previous_text = joined[-1]
            joined[-1] = (previous_indent, f"{previous_text} {text}".strip())
            continue
        joined.append((indent, text))
    return [f"{' ' * indent}{text}" for indent, text in joined if text]


def _wraps(previous: tuple[int, str], current: tuple[int, str]) -> bool:
    """Whether ``current`` continues ``previous`` (never dedented, unterminated)."""
    previous_indent, previous_text = previous
    current_indent, current_text = current
    if not previous_text or not current_text:
        return False
    if current_indent < previous_indent:
        return False
    return not previous_text.rstrip().endswith(STATEMENT_TERMINATORS)


def _indent_columns(offset: float, size: float) -> int:
    """Estimated leading columns for a left-edge offset, clamped and never negative."""
    width = SPACE_WIDTH_RATIO * size
    if width <= 0 or offset <= 0:
        return 0
    return min(round(offset / width), MAX_INDENT_COLUMNS)
