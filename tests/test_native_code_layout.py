"""Layout recovery v1 (pc-0uv): positioned lines → PARAGRAPH and CODE chunks.

Pure unit tests: PyMuPDF is never imported, the vendor page dictionary is built
by hand (which is the point of keeping every rule in ``code_layout``), and the
AGPL ``pdf`` extra stays out of the canonical dev environment.
"""

from __future__ import annotations

import pytest

from parsecraft.backends.native import pdf as pdf_module
from parsecraft.backends.native.code_layout import (
    CODE_MASS_THRESHOLD,
    FEATURE_CODE,
    MAX_INDENT_COLUMNS,
    MIN_CODE_CHARS,
    SPACE_WIDTH_RATIO,
    PageLayout,
    PageText,
    TextLine,
    TextSpan,
    build_chunks,
    is_monospace_font,
    page_text,
)
from parsecraft.backends.protocol import BackendConfig, ConversionRequest, SourceDocument
from parsecraft.ir import render_page
from parsecraft.ir.models import ChunkKind, DiagnosticLevel
from tests.fixtures.documents import code_pdf, minimal_pdf

_PROSE_FONT = "ABCDEF+NimbusRoman-Regular"
_CODE_FONT = "ABCDEF+NimbusMonoPS-Regular"
_PAGE = 7
_PREFIX = "native-pdf"


# ── Integration: the real PyMuPDF path (pdf extra, AGPL — ADR-0003) ──────────


def test_real_pdf_code_block_is_fenced_end_to_end() -> None:
    """The acceptance shape on a real PDF: positioned Courier → one fenced CODE chunk.

    Skips in the canonical dev environment, which deliberately lacks the AGPL
    ``pdf`` extra; run it with
    ``uv run --isolated --extra pdf --extra pdf-lite pytest -k code_block_is_fenced``.
    """
    pytest.importorskip("pymupdf", reason="pdf extra (PyMuPDF, AGPL) not installed")
    pytest.importorskip("pypdf", reason="pdf-lite extra absent in the light env")

    data = code_pdf()
    backend = pdf_module.factory(BackendConfig(name="native-pdf"))
    result = backend.convert(ConversionRequest(source=SourceDocument(uri="file:///code.pdf", media_type="application/pdf", content=data)))

    assert result.failures == []
    page = result.pages[0]
    assert [chunk.kind for chunk in page.blocks] == [ChunkKind.PARAGRAPH, ChunkKind.CODE, ChunkKind.PARAGRAPH]
    code = page.blocks[1]
    # Geometry, not spaces, carries the layout: indentation is re-derived, the
    # wrapped declaration is joined, the terminated line is not, and `}` dedents.
    assert code.content == "void f(void) {\n  return;\nunsigned long mulMode;\n}"
    assert code.metadata == {"language": ""}
    assert [diagnostic.code for diagnostic in page.diagnostics] == [FEATURE_CODE]

    markdown = render_page(page)
    assert f"```\n{code.content}\n```" in markdown
    assert markdown.count("```") == 2  # exactly one fenced block on this page


def test_real_pdf_prose_only_page_has_no_code() -> None:
    """The other direction: prose fonts must not be mistaken for code."""
    pytest.importorskip("pymupdf", reason="pdf extra (PyMuPDF, AGPL) not installed")
    pytest.importorskip("pypdf", reason="pdf-lite extra absent in the light env")

    data = minimal_pdf()
    backend = pdf_module.factory(BackendConfig(name="native-pdf"))
    result = backend.convert(ConversionRequest(source=SourceDocument(uri="file:///prose.pdf", media_type="application/pdf", content=data)))
    assert result.failures == []
    kinds = {chunk.kind for page in result.pages for chunk in page.blocks}
    assert kinds == {ChunkKind.PARAGRAPH}
    assert all(not page.diagnostics for page in result.pages)


# ── Font classification ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "font",
    [
        "ABCDEF+NimbusMonoPS-Regular",
        "Courier",
        "CourierNewPS-BoldMT",
        "Consolas",
        "DejaVuSansMono",
        "Menlo-Regular",
        "CascadiaCode",
        "SourceCodePro-Regular",
        "JetBrainsMono-Regular",
        "Andale Mono",
        "LucidaConsole",
        "Fira Code Retina",
        "Inconsolata",
        "Monaco",
    ],
)
def test_monospace_fonts_are_detected(font: str) -> None:
    assert is_monospace_font(font) is True


@pytest.mark.parametrize(
    "font",
    [
        "Helvetica",
        "NimbusRoman-Regular",
        "Times-Bold",
        "Arial",
        "MonotypeCorsiva",  # contains "mono" but is a proportional script
        "Monotype Sorts",
        "",
    ],
)
def test_proportional_fonts_are_not_monospace(font: str) -> None:
    assert is_monospace_font(font) is False


# ── Vendor dictionary mapping ───────────────────────────────────────────────


def test_page_text_maps_the_vendor_dictionary() -> None:
    page = page_text(
        {
            "width": 595.0,  # unknown keys are ignored
            "blocks": [
                {
                    "type": 0,
                    "bbox": (0.0, 0.0, 100.0, 20.0),
                    "lines": [
                        {
                            "bbox": (40.0, 10.0, 90.0, 20.0),
                            "spans": [
                                {"font": _CODE_FONT, "size": 10.0, "bbox": (40.0, 10.0, 60.0, 20.0), "text": "int "},
                                {"font": _CODE_FONT, "size": 10.0, "bbox": (60.0, 10.0, 90.0, 20.0), "text": "main"},
                            ],
                        }
                    ],
                },
                {"type": 1, "bbox": (0.0, 0.0, 10.0, 10.0)},  # an image block: no lines
            ],
        }
    )
    assert len(page.lines) == 1
    line = page.lines[0]
    assert line.text == "int main"
    assert line.x0 == 40.0
    assert (line.y0, line.y1, line.height) == (10.0, 20.0, 10.0)
    assert line.size == 10.0


def test_page_text_drops_spans_without_geometry_or_text() -> None:
    page = page_text(
        {
            "blocks": [
                {
                    "lines": [
                        {
                            "bbox": (0.0, 0.0, 10.0, 10.0),
                            "spans": [
                                {"font": _PROSE_FONT, "size": 0.0, "bbox": (0.0, 0.0, 5.0, 5.0), "text": "no size"},
                                {"font": _PROSE_FONT, "size": 12.0, "bbox": (0.0, 0.0, 5.0, 5.0), "text": ""},
                                {"font": _PROSE_FONT, "size": 12.0, "bbox": (0.0, 0.0, 20.0, 12.0), "text": "kept"},
                            ],
                        },
                        {"bbox": (0.0, 0.0, 10.0, 10.0), "spans": []},
                    ],
                }
            ]
        }
    )
    assert [line.text for line in page.lines] == ["kept"]


def test_page_text_accepts_an_empty_dictionary() -> None:
    assert page_text({}).lines == ()
    assert build_chunks(PageText(lines=()), page_number=1).chunks == []


# ── Paragraph reconstruction (unchanged behaviour) ──────────────────────────


def test_prose_lines_join_into_one_paragraph() -> None:
    layout = build_chunks(_page(_line("first", y=0.0), _line("second", y=12.0)), page_number=_PAGE, prefix=_PREFIX)
    assert [chunk.content for chunk in layout.chunks] == ["first second"]
    assert layout.chunks[0].kind is ChunkKind.PARAGRAPH
    assert layout.code_mass == 0.0
    assert layout.diagnostics == []


def test_a_vertical_gap_breaks_the_paragraph() -> None:
    """A blank line has no glyphs, so the gap is what the text layer shows."""
    layout = build_chunks(
        _page(_line("first", y=0.0), _line("second", y=30.0)),
        page_number=_PAGE,
        prefix=_PREFIX,
    )
    assert [chunk.content for chunk in layout.chunks] == ["first", "second"]


def test_an_explicit_blank_line_breaks_the_paragraph() -> None:
    layout = build_chunks(_page(_line("first", y=0.0), _line("   ", y=12.0), _line("second", y=24.0)), page_number=_PAGE, prefix=_PREFIX)
    assert [chunk.content for chunk in layout.chunks] == ["first", "second"]


def test_chunk_ids_and_reading_order_are_deterministic() -> None:
    layout = build_chunks(_page(_line("first", y=0.0), _line("second", y=30.0)), page_number=3, prefix=_PREFIX)
    assert [chunk.id for chunk in layout.chunks] == [f"{_PREFIX}-3-b0", f"{_PREFIX}-3-b1"]
    assert [chunk.reading_order for chunk in layout.chunks] == [0, 1]
    assert all(chunk.page_number == 3 for chunk in layout.chunks)


# ── CODE detection and grouping ─────────────────────────────────────────────


def test_a_monospace_run_becomes_one_code_chunk() -> None:
    size = 10.0
    step = SPACE_WIDTH_RATIO * size
    layout = build_chunks(
        _page(
            _code_line("void f(void) {", x0=40.0, y=0.0, size=size),
            _code_line("return;", x0=40.0 + 2 * step, y=10.0, size=size),
            _code_line("}", x0=40.0, y=20.0, size=size),
        ),
        page_number=_PAGE,
        prefix=_PREFIX,
    )
    assert [chunk.kind for chunk in layout.chunks] == [ChunkKind.CODE]
    code = layout.chunks[0]
    assert code.content == "void f(void) {\n  return;\n}"
    assert code.metadata == {"language": ""}  # plain fence: no language is invented
    assert layout.code_mass == 1.0
    assert [diagnostic.code for diagnostic in layout.diagnostics] == [FEATURE_CODE]
    assert layout.diagnostics[0].level is DiagnosticLevel.INFO


def test_code_and_prose_interleave_in_reading_order() -> None:
    layout = build_chunks(
        _page(
            _line("The call below returns immediately.", y=0.0),
            _code_line("return 0;", y=20.0),
            _line("That is all.", y=40.0),
        ),
        page_number=_PAGE,
        prefix=_PREFIX,
    )
    assert [chunk.kind for chunk in layout.chunks] == [ChunkKind.PARAGRAPH, ChunkKind.CODE, ChunkKind.PARAGRAPH]
    assert [chunk.content for chunk in layout.chunks] == ["The call below returns immediately.", "return 0;", "That is all."]


def test_a_prose_line_with_one_monospace_word_is_not_code() -> None:
    """The line must be *mostly* monospace — a quoted identifier is not code."""
    mixed = TextLine(
        y0=0.0,
        y1=12.0,
        spans=(
            _span("Call the helper ", x0=40.0),
            _span("do_work", font=_CODE_FONT, x0=120.0),
            _span(" once.", x0=160.0),
        ),
    )
    layout = build_chunks(_page(mixed), page_number=_PAGE, prefix=_PREFIX)
    assert [chunk.kind for chunk in layout.chunks] == [ChunkKind.PARAGRAPH]


def test_code_mass_below_the_threshold_emits_no_hint() -> None:
    prose = "x" * 400
    layout = build_chunks(_page(_line(prose, y=0.0), _code_line("int x;", y=20.0)), page_number=_PAGE, prefix=_PREFIX)
    assert 0.0 < layout.code_mass < CODE_MASS_THRESHOLD
    assert layout.diagnostics == []


def test_a_tiny_monospace_run_is_not_a_code_chunk() -> None:
    layout = build_chunks(_page(_code_line("x", y=0.0)), page_number=_PAGE, prefix=_PREFIX)
    assert layout.chunks == [] or all(chunk.kind is not ChunkKind.CODE for chunk in layout.chunks)
    assert len("x") < MIN_CODE_CHARS  # documents why: too little to be a block


def test_a_non_monospace_line_ends_the_code_run() -> None:
    layout = build_chunks(
        _page(_code_line("int a;", y=0.0), _line("Prose in between.", y=20.0), _code_line("int b;", y=40.0)),
        page_number=_PAGE,
        prefix=_PREFIX,
    )
    assert [chunk.kind for chunk in layout.chunks] == [ChunkKind.CODE, ChunkKind.PARAGRAPH, ChunkKind.CODE]
    assert [chunk.content for chunk in layout.chunks] == ["int a;", "Prose in between.", "int b;"]


# ── Indentation from x0 deltas ──────────────────────────────────────────────


def test_relative_indentation_comes_from_x0_deltas() -> None:
    size = 10.0
    step = SPACE_WIDTH_RATIO * size  # one estimated column
    layout = build_chunks(
        _page(
            _code_line("void f(void) {", x0=40.0, y=0.0, size=size),
            _code_line("return;", x0=40.0 + 2 * step, y=10.0, size=size),
            _code_line("}", x0=40.0, y=20.0, size=size),
        ),
        page_number=_PAGE,
        prefix=_PREFIX,
    )
    assert layout.chunks[0].content == "void f(void) {\n  return;\n}"


def test_indentation_is_clamped_and_never_negative() -> None:
    size = 10.0
    layout = build_chunks(
        _page(
            _code_line("base;", x0=100.0, y=0.0, size=size),
            _code_line("far", x0=100.0 + (MAX_INDENT_COLUMNS + 5) * SPACE_WIDTH_RATIO * size, y=10.0, size=size),
        ),
        page_number=_PAGE,
        prefix=_PREFIX,
    )
    lines = layout.chunks[0].content.splitlines()
    assert lines[0] == "base;"
    assert lines[1] == f"{' ' * MAX_INDENT_COLUMNS}far"
    # A line left of the block's base (negative offset) is not outdented:
    leftmost = build_chunks(
        _page(_code_line("base;", x0=100.0, y=0.0, size=size), _code_line("left", x0=90.0, y=10.0, size=size)),
        page_number=_PAGE,
        prefix=_PREFIX,
    )
    assert leftmost.chunks[0].content.splitlines()[1] == "left"


# ── Wrapped-line join ───────────────────────────────────────────────────────


def test_a_wrapped_declaration_is_joined() -> None:
    """The POLQA shape: the first half ends in a space, the second continues it."""
    layout = build_chunks(
        _page(
            _code_line("unsigned long ", x0=40.0, y=0.0),
            _code_line("mulMode; ", x0=40.0, y=10.0),
        ),
        page_number=_PAGE,
        prefix=_PREFIX,
    )
    assert layout.chunks[0].content == "unsigned long mulMode;"


def test_a_terminated_line_is_never_joined() -> None:
    layout = build_chunks(
        _page(_code_line("int a;", x0=40.0, y=0.0), _code_line("int b;", x0=40.0, y=10.0)),
        page_number=_PAGE,
        prefix=_PREFIX,
    )
    assert layout.chunks[0].content == "int a;\nint b;"


def test_a_dedented_line_is_never_joined() -> None:
    size = 10.0
    layout = build_chunks(
        _page(
            _code_line("if (x)", x0=40.0 + 2 * SPACE_WIDTH_RATIO * size, y=0.0, size=size),
            _code_line("done", x0=40.0, y=10.0, size=size),
        ),
        page_number=_PAGE,
        prefix=_PREFIX,
    )
    assert layout.chunks[0].content == "  if (x)\ndone"


def test_a_deeper_indented_continuation_is_joined() -> None:
    size = 10.0
    layout = build_chunks(
        _page(
            _code_line("int total =", x0=40.0, y=0.0, size=size),
            _code_line("a + b;", x0=40.0 + 4 * SPACE_WIDTH_RATIO * size, y=10.0, size=size),
        ),
        page_number=_PAGE,
        prefix=_PREFIX,
    )
    assert layout.chunks[0].content == "int total = a + b;"


# ── Diagnostics ─────────────────────────────────────────────────────────────


def test_code_mass_reports_the_share_of_code_characters() -> None:
    layout = build_chunks(
        _page(_code_line("int a;", y=0.0), _line("prose " * 3, y=20.0)),
        page_number=_PAGE,
        prefix=_PREFIX,
    )
    total = sum(len(chunk.content) for chunk in layout.chunks)
    code = len(layout.chunks[0].content)
    assert layout.code_mass == pytest.approx(code / total)


def _page(*lines: TextLine) -> PageText:
    return PageText(lines=lines)


def _code_line(text: str, *, x0: float = 40.0, y: float = 0.0, size: float = 10.0) -> TextLine:
    return _line(text, font=_CODE_FONT, size=size, x0=x0, y=y)


def _line(text: str, *, font: str = _PROSE_FONT, size: float = 12.0, x0: float = 40.0, y: float = 0.0) -> TextLine:
    """One single-span line whose height equals its font size."""
    return TextLine(y0=y, y1=y + size, spans=(_span(text, font=font, size=size, x0=x0),))


def _span(text: str, *, font: str = _PROSE_FONT, size: float = 12.0, x0: float = 40.0) -> TextSpan:
    return TextSpan(font=font, size=size, x0=x0, x1=x0 + 6.0 * len(text), text=text)


def test_hint_message_names_the_share() -> None:
    layout = PageLayout(chunks=[], code_mass=0.42)
    assert [diagnostic.message for diagnostic in layout.diagnostics] == ["42% of this page's text is monospace code (fenced in the Markdown projection)"]


def test_no_code_means_no_mass() -> None:
    layout = build_chunks(PageText(lines=()), page_number=_PAGE, prefix=_PREFIX)
    assert layout.code_mass == 0.0
