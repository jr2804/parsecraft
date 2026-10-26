"""Content-stage normalization for the native MinerU2.5 protocol — pure functions.

OTSL/markdown table conversion, tagged image-analysis fields, equation
wrapping, and the HTML table flattening are pinned against hand-written
model outputs (offline, no model involved).
"""

from __future__ import annotations

from parsecraft.backends.mineru._content import (
    TableTextExtractor,
    is_non_text,
    is_separator_line,
    markdown_table_to_html,
    otsl_to_html,
    parse_image_analysis,
    wrap_equation,
)


def test_otsl_to_html_passes_html_through() -> None:
    assert otsl_to_html("<table><tr><td>x</td></tr></table>") == "<table><tr><td>x</td></tr></table>"


def test_otsl_to_html_builds_a_grid() -> None:
    otsl = "<fcel>a<ecel><nl><fcel>b<ecel>"
    assert otsl_to_html(otsl) == "<table><tr><td>a</td><td></td></tr><tr><td>b</td><td></td></tr></table>"


def test_otsl_to_html_span_tokens_open_empty_slots() -> None:
    # Every cell token is one grid slot: fcel carries text, lcel/ucel/xcel and
    # ecel are empty slots. "head + span-continuation + empty" is a 3-wide row.
    otsl = "<fcel>head<lcel><ecel><nl><fcel>a<ecel>"
    assert otsl_to_html(otsl) == ("<table><tr><td>head</td><td></td><td></td></tr><tr><td>a</td><td></td><td></td></tr></table>")


def test_otsl_to_html_escapes_cell_text() -> None:
    # The trailing <ecel> is a real (empty) slot after the filled cell.
    assert otsl_to_html("<fcel>a<b & c<ecel>") == "<table><tr><td>a&lt;b &amp; c</td><td></td></tr></table>"


def test_otsl_to_html_empty_and_free_text_degrade() -> None:
    assert otsl_to_html("") == ""
    # Free text without structural tokens becomes a single phantom cell —
    # honest degradation, never a crash.
    assert otsl_to_html("just prose") == "<table><tr><td>just prose</td></tr></table>"


def test_is_separator_line() -> None:
    assert is_separator_line("| --- | --- |") is True
    assert is_separator_line("|:---|:---:|---:|") is True
    assert is_separator_line("| a | b |") is False
    assert is_separator_line("no pipes") is False


def test_markdown_table_to_html_converts() -> None:
    table = "| a | b |\n| --- | --- |\n| 1 | 2 |"
    assert markdown_table_to_html(table) == "<table><tr><td>a</td><td>b</td></tr><tr><td>1</td><td>2</td></tr></table>"


def test_markdown_table_to_html_keeps_escaped_pipes() -> None:
    table = "| a \\| b | c |\n| --- | --- |\n| 1 | 2 |"
    assert markdown_table_to_html(table) == "<table><tr><td>a | b</td><td>c</td></tr><tr><td>1</td><td>2</td></tr></table>"


def test_markdown_table_to_html_rejects_non_tables() -> None:
    assert markdown_table_to_html("just prose") is None
    assert markdown_table_to_html("| only | header |") is None
    assert markdown_table_to_html("| a | b |\nnot a separator") is None
    assert markdown_table_to_html("| a | b |\n| --- | --- |\n|  |  |") is None


def test_parse_image_analysis_extracts_tagged_fields() -> None:
    content = (
        "<|class_start|>chart<|class_end|><|sub_class_start|>bar<|sub_end|><|caption_start|>Figure 1<|caption_end|><|content_start|>markdown<|content_end|>"
    )
    fields = parse_image_analysis(content)
    assert fields == {"class": "chart", "caption": "Figure 1", "content": "markdown"}


def test_parse_image_analysis_ignores_unknown_fields() -> None:
    fields = parse_image_analysis("<|mood_start|>bright<|mood_end|>")
    assert fields == {}


def test_parse_image_analysis_unclosed_content_tag_takes_the_tail() -> None:
    fields = parse_image_analysis("<|class_start|>chart<|class_end|><|content_start|>everything after")
    assert fields == {"class": "chart", "content": "everything after"}


def test_parse_image_analysis_no_fallback_with_multiple_openers() -> None:
    fields = parse_image_analysis("<|content_start|>one<|content_start|>two")
    assert fields == {}


def test_wrap_equation_wraps_plain_text() -> None:
    assert wrap_equation("E = mc^2") == "\\[ E = mc^2 \\]"


def test_wrap_equation_leaves_wrapped_text_alone() -> None:
    assert wrap_equation("\\[ E = mc^2 \\]") == "\\[ E = mc^2 \\]"


def test_is_non_text() -> None:
    assert is_non_text(None) is True
    assert is_non_text("") is True
    assert is_non_text("   ") is True
    assert is_non_text("[Non-Text]") is True
    assert is_non_text("words") is False


def test_table_text_extractor_flattens_rows() -> None:
    extractor = TableTextExtractor()
    extractor.feed("<table><tr><td>a</td><td>b</td></tr><tr><td>1</td><td>2</td></tr></table>")
    extractor.close()
    assert extractor.render() == "a | b\n1 | 2"
    assert extractor.rows() == (("a", "b"), ("1", "2"))


def test_table_text_extractor_squashes_nested_markup() -> None:
    extractor = TableTextExtractor()
    extractor.feed("<table><tr><td><b>bold</b>  text</td><th>head</th></tr></table>")
    extractor.close()
    assert extractor.render() == "bold text | head"


def test_table_text_extractor_drops_empty_rows() -> None:
    extractor = TableTextExtractor()
    extractor.feed("<table><tr><td></td><td> </td></tr><tr><td>x</td></tr></table>")
    extractor.close()
    assert extractor.render() == "x"
