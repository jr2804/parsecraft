"""Offline tests for the native backend family (text/markdown/html/csv)."""

from __future__ import annotations

import csv
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from markdown_it import MarkdownIt

from parsecraft.adapters.classification import TextKind, register_text_classifier_provider
from parsecraft.backends import pymupdf as pymupdf_module
from parsecraft.backends.native import csv as csv_module
from parsecraft.backends.native import html as html_module
from parsecraft.backends.native import markdown as markdown_module
from parsecraft.backends.native import text as text_module
from parsecraft.backends.native._common import (
    NATIVE_BACKEND_VERSION,
    DependencyUnavailableError,
    NativeBackendBase,
    SourceReadError,
    no_images,
    one_page,
    paragraph_chunks,
    source_bytes,
    split_paragraphs,
)
from parsecraft.backends.protocol import (
    BackendConfig,
    ConversionRequest,
    SourceDocument,
)
from parsecraft.backends.registry import BackendRegistry
from parsecraft.cli import config as cli_config
from parsecraft.ir.models import ChunkKind, DiagnosticLevel, FailureCode, PageRange, PageResult
from parsecraft.pipeline.analysis import media_type_for
from tests.fixtures.documents import csv_bytes, document_bytes, tsv_bytes

# ── NativeBackendBase flow (via a stub backend) ────────────────────────────


class _StubBackend(NativeBackendBase):
    """Minimal concrete subclass for exercising the shared flow."""

    name = "native-stub"


class _StubTextClassifier:
    """Structural TextKindClassifier returning a canned verdict."""

    def __init__(self, verdict: tuple[TextKind, float]) -> None:
        self._verdict = verdict

    def classify_text(self, text: str) -> tuple[TextKind, float]:
        return self._verdict


# ── _common: source reading, helpers ───────────────────────────────────────


def test_source_bytes_prefers_content() -> None:
    assert source_bytes(_source(b"inline")) == b"inline"


def test_source_bytes_reads_real_file_from_pytest_cache(cache_subdir: Path) -> None:
    path = cache_subdir / "native-source.txt"
    path.write_bytes(b"from disk\n\nsecond block")
    loaded = source_bytes(SourceDocument(uri=str(path), content=None))
    assert loaded == b"from disk\n\nsecond block"


def test_source_bytes_strips_file_uri_scheme(cache_subdir: Path) -> None:
    path = cache_subdir / "native-uri.txt"
    path.write_bytes(b"via file uri")
    loaded = source_bytes(SourceDocument(uri=f"file://{path}", content=None))
    assert loaded == b"via file uri"


def test_source_bytes_missing_file_raises_typed_error() -> None:
    missing = "Z:\\definitely\\missing\\native-nope.txt"
    with pytest.raises(SourceReadError, match="cannot read source document"):
        source_bytes(SourceDocument(uri=missing, content=None))


def test_split_paragraphs_and_paragraph_chunks() -> None:
    assert split_paragraphs("  \n  ") == []
    assert split_paragraphs("a\n\nb") == ["a", "b"]
    chunks = paragraph_chunks("pfx", 3, "one\n\ntwo")
    assert [c.id for c in chunks] == ["pfx-3-b0", "pfx-3-b1"]
    assert [c.reading_order for c in chunks] == [0, 1]
    assert all(c.page_number == 3 and c.kind is ChunkKind.PARAGRAPH for c in chunks)


def test_default_counters() -> None:
    assert no_images(b"<img>") == 0
    assert one_page(b"anything") == 1


# ── native-text ────────────────────────────────────────────────────────────


def test_text_descriptor_contract() -> None:
    descriptor = text_module.factory.descriptor
    assert descriptor.name == "native-text"
    assert descriptor.version == NATIVE_BACKEND_VERSION
    assert descriptor.capabilities.supported_formats == ["text/plain"]
    assert descriptor.capabilities.supports_page_ranges is False
    assert descriptor.capabilities.supports_multi_page is False
    assert descriptor.capabilities.gpu_requirement == 0.0
    assert descriptor.capabilities.optional_dependency_group is None


def test_text_convert_splits_blank_line_blocks() -> None:
    backend = text_module.factory(_config("native-text"))
    result = backend.convert(_request(b"alpha\n\nbeta\n\n gamma "))
    assert _kinds(result) == [ChunkKind.PARAGRAPH, ChunkKind.PARAGRAPH, ChunkKind.PARAGRAPH]
    assert _contents(result) == ["alpha", "beta", "gamma"]
    assert [b.reading_order for b in result.pages[0].blocks] == [0, 1, 2]
    assert result.backend.name == "native-text"
    assert result.backend.version == NATIVE_BACKEND_VERSION
    assert result.failures == []


def test_text_convert_empty_input_yields_empty_page() -> None:
    backend = text_module.factory(_config("native-text"))
    result = backend.convert(_request(b"   \n\n  "))
    assert result.pages[0].blocks == []


def test_text_analyze_signals_and_hash() -> None:
    backend = text_module.factory(_config("native-text"))
    analysis = backend.analyze(_source(b"hello world"))
    assert analysis.page_count == 1
    assert analysis.source_hash == hashlib.sha256(b"hello world").hexdigest()
    signal = analysis.signals[0]
    assert signal.has_native_text is True
    assert signal.text_chars == len(b"hello world")
    assert signal.image_count == 0
    assert signal.blank is False


def test_text_analyze_blank_input() -> None:
    backend = text_module.factory(_config("native-text"))
    signal = backend.analyze(_source(b"  \n ")).signals[0]
    assert signal.blank is True
    assert signal.has_native_text is False


def test_text_page_range_outside_single_page_returns_no_pages() -> None:
    backend = text_module.factory(_config("native-text"))
    request = ConversionRequest(
        source=_source(b"only page one"),
        page_range=PageRange(start=2, end=5),
    )
    result = backend.convert(request)
    assert result.pages == []
    assert result.failures == []


def test_text_convert_from_disk_file(cache_subdir: Path) -> None:
    path = cache_subdir / "native-convert.txt"
    path.write_text("on disk", encoding="utf-8")
    backend = text_module.factory(_config("native-text"))
    result = backend.convert(ConversionRequest(source=SourceDocument(uri=str(path), content=None)))
    assert _contents(result) == ["on disk"]


@pytest.fixture(autouse=True)
def _isolated_judge_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """extract_text_pages resolves the global judge config — never a real one."""
    monkeypatch.delenv("PARSECRAFT_JUDGE_MODEL", raising=False)
    monkeypatch.setattr(cli_config, "global_config_path", lambda: tmp_path / "global" / "config.toml")


def test_text_extract_unconfigured_matches_paragraph_chunks() -> None:
    content = b"alpha\n\nbeta"
    page = text_module.extract_text_pages(_source(content), content)[0]
    assert page == PageResult(page_number=1, blocks=paragraph_chunks("native-text", 1, "alpha\n\nbeta"))


def test_text_extract_configured_markdown_like_stamps_verdict(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    register_text_classifier_provider("stub-text-kind-9p65", lambda spec: _StubTextClassifier((TextKind.MARKDOWN_LIKE, 0.9)))
    _write_text_classifier_config(tmp_path, "stub-text-kind-9p65/any-model")
    pages = text_module.extract_text_pages(_source(b"# Hi\n", uri="mem://wired"), b"# Hi\n")
    assert [block.kind for block in pages[0].blocks] == [ChunkKind.HEADING]
    assert [block.metadata.get("detected_kind") for block in pages[0].blocks] == ["markdown-like"]
    assert [block.confidence for block in pages[0].blocks] == [0.9]
    assert [block.id for block in pages[0].blocks] == ["md-0000-heading"]
    assert pages[0].diagnostics[0].level is DiagnosticLevel.INFO


def test_text_extract_configured_plain_stamps_plain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    register_text_classifier_provider("stub-text-kind-plain", lambda spec: _StubTextClassifier((TextKind.PLAIN, 1.0)))
    _write_text_classifier_config(tmp_path, "stub-text-kind-plain/model")
    pages = text_module.extract_text_pages(_source(b"just words"), b"just words")
    assert [block.kind for block in pages[0].blocks] == [ChunkKind.PARAGRAPH]
    assert [block.metadata.get("detected_kind") for block in pages[0].blocks] == ["plain"]
    assert [block.confidence for block in pages[0].blocks] == [1.0]


def test_text_extract_unavailable_provider_falls_back_quietly(tmp_path: Path) -> None:
    # No text-classifier loader exists for routing providers (pc-9p6.3
    # finding): an operator's working routing config is not their mistake,
    # so the fallback stays at INFO, not WARNING.
    _write_text_classifier_config(tmp_path, "no-such-provider-9p65/model")
    pages = text_module.extract_text_pages(_source(b"hello"), b"hello")
    assert pages[0].blocks == paragraph_chunks("native-text", 1, "hello")
    (diagnostic,) = pages[0].diagnostics
    assert diagnostic.level is DiagnosticLevel.INFO
    assert diagnostic.code == "text-kind"
    assert "unusable" in diagnostic.message


def test_text_extract_malformed_spec_stays_warning(tmp_path: Path) -> None:
    _write_text_classifier_config(tmp_path, "a:")
    pages = text_module.extract_text_pages(_source(b"hello"), b"hello")
    assert pages[0].blocks == paragraph_chunks("native-text", 1, "hello")
    (diagnostic,) = pages[0].diagnostics
    assert diagnostic.level is DiagnosticLevel.WARNING
    assert diagnostic.code == "text-kind"
    assert "unusable" in diagnostic.message


def test_text_extract_ignores_a_routing_judge_config(tmp_path: Path) -> None:
    """pc-9p6.5 acceptance 1: a routing ``[judge]`` model must not switch on classification."""
    config_file = tmp_path / "global" / "config.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text('[judge]\nmodel = "typesafe-ai/jev-latest"\n', encoding="utf-8")
    page = text_module.extract_text_pages(_source(b"alpha\n\nbeta"), b"alpha\n\nbeta")[0]
    assert page == PageResult(page_number=1, blocks=paragraph_chunks("native-text", 1, "alpha\n\nbeta"))
    assert page.diagnostics == []


def test_text_extract_parse_failure_falls_back_quietly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    register_text_classifier_provider("stub-text-kind-boom", lambda spec: _StubTextClassifier((TextKind.MARKDOWN_LIKE, 1.0)))
    _write_text_classifier_config(tmp_path, "stub-text-kind-boom/model")

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("parse exploded")

    monkeypatch.setattr(text_module, "parse_ambiguous_text", _boom)
    pages = text_module.extract_text_pages(_source(b"# Hi"), b"# Hi")
    assert pages[0].blocks == paragraph_chunks("native-text", 1, "# Hi")
    (diagnostic,) = pages[0].diagnostics
    assert diagnostic.level is DiagnosticLevel.INFO
    assert "parse failed" in diagnostic.message


def _write_text_classifier_config(tmp_path: Path, model: str) -> None:
    """Point the isolated global config at a ``[text_classifier]`` model."""
    config_file = tmp_path / "global" / "config.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(f'[text_classifier]\nmodel = "{model}"\n', encoding="utf-8")


def test_text_kind_classification_import_pulls_no_cli() -> None:
    # The subprocess must import THIS checkout: the ambient interpreter
    # resolves parsecraft to the shared checkout, which lacks new modules.
    src_dir = Path(__file__).resolve().parents[2] / "src"
    env = {**os.environ, "PYTHONPATH": str(src_dir)}
    script = """
import sys

import parsecraft.adapters.classification

cli = sorted(m for m in sys.modules if m == "parsecraft.cli" or m.startswith("parsecraft.cli."))
assert not cli, f"cli pulled: {cli}"
print("CLASSIFICATION_IMPORTS_OK")
"""
    proc = subprocess.run(  # noqa: S603 - trusted local interpreter, fixed argv, no shell
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "CLASSIFICATION_IMPORTS_OK" in proc.stdout


def test_media_type_for_ignores_detected_kind_even_for_markdown_bytes() -> None:
    assert media_type_for(Path("note.txt")) == "text/plain"
    pages = text_module.extract_text_pages(_source(b"# Hi\n\npara"), b"# Hi\n\npara")
    assert all(block.kind is ChunkKind.PARAGRAPH for block in pages[0].blocks)
    assert all("detected_kind" not in block.metadata for block in pages[0].blocks)


# ── native-markdown ────────────────────────────────────────────────────────


def test_markdown_descriptor_contract() -> None:
    descriptor = markdown_module.factory.descriptor
    assert descriptor.name == "native-markdown"
    assert descriptor.version == NATIVE_BACKEND_VERSION
    assert descriptor.capabilities.supported_formats == ["text/markdown"]
    assert descriptor.capabilities.supports_multi_page is False


def test_markdown_convert_delegates_to_input_adapter() -> None:
    backend = markdown_module.factory(_config("native-markdown"))
    result = backend.convert(_request(document_bytes("md")))
    assert _kinds(result)[0] is ChunkKind.HEADING
    assert result.pages[0].blocks[0].metadata["heading_level"] == "1"
    assert any(kind is ChunkKind.LIST for kind in _kinds(result))
    assert result.failures == []


def test_markdown_analyze_single_page() -> None:
    backend = markdown_module.factory(_config("native-markdown"))
    analysis = backend.analyze(_source(document_bytes("md")))
    assert analysis.page_count == 1
    assert analysis.signals[0].image_count == 0


def test_markdown_backend_survives_pdf_shaped_cr_bytes() -> None:
    r"""pc-9p6.12: PDF bodies legally use CR-only line endings (PDF spec §7.5).

    Presented as .md and replacement-decoded, the old LF-only offset table
    raised IndexError in the adapter's ``_span``. The backend must return
    pages — the ``\xff`` byte exercises the replacement-decoding path,
    the ``# tail`` heading proves content past the CR block survives.
    """
    objs = "".join(f"{i} 0 obj\r<< /Comment (\xff) >>\rendobj\r" for i in range(1, 9))
    pdf_bytes = ("%PDF-1.4\n" + objs + "xref\r0 9\r0000000000 65535 f\r" + "\n\n# tail\n").encode("latin-1")
    backend = markdown_module.factory(_config("native-markdown"))
    result = backend.convert(_request(pdf_bytes))
    assert result.failures == []
    assert any(block.kind is ChunkKind.HEADING and block.content == "tail" for block in result.pages[0].blocks)


# ── native-html ────────────────────────────────────────────────────────────


def test_html_descriptor_contract() -> None:
    descriptor = html_module.factory.descriptor
    assert descriptor.name == "native-html"
    assert descriptor.version == NATIVE_BACKEND_VERSION
    assert descriptor.capabilities.supported_formats == ["text/html"]


def test_html_full_document_mapping() -> None:
    html = (
        "<html><head><title>Doc title</title></head><body>"
        "<h1>Chapter</h1>"
        "<p>Para with <b>bold</b> and <i>italic</i>.</p>"
        "<ul><li>alpha</li><li>beta</li></ul>"
        "<ol><li>first</li><li>second</li></ol>"
        "<table><tr><th>id</th><th>name</th></tr><tr><td>1</td><td>one</td></tr></table>"
        "<blockquote>deep thought</blockquote>"
        '<pre><code class="language-python">x = 1</code></pre>'
        "<p>trailing paragraph</p>"
        "</body></html>"
    )
    backend = html_module.factory(_config("native-html"))
    result = backend.convert(_request(html.encode()))
    kinds = _kinds(result)
    assert kinds == [
        ChunkKind.HEADING,
        ChunkKind.PARAGRAPH,
        ChunkKind.LIST,
        ChunkKind.LIST,
        ChunkKind.TABLE,
        ChunkKind.QUOTE,
        ChunkKind.CODE,
        ChunkKind.PARAGRAPH,
    ]
    blocks = result.pages[0].blocks
    assert blocks[0].metadata == {"heading_level": "1"}
    assert blocks[2].metadata == {"list_type": "bullet"}
    assert blocks[3].metadata == {"list_type": "ordered"}
    assert blocks[4].content == "| id | name |\n| --- | --- |\n| 1 | one |"
    assert blocks[5].content == "deep thought"
    assert blocks[6].content == "x = 1"
    assert blocks[6].metadata == {"language": "python"}
    assert blocks[7].content == "trailing paragraph"
    assert [b.reading_order for b in blocks] == list(range(len(blocks)))


def test_html_head_content_is_skipped_with_diagnostic() -> None:
    backend = html_module.factory(_config("native-html"))
    html = "<head><title>T</title></head><p>visible</p>"
    result = backend.convert(_request(html.encode()))
    assert _kinds(result) == [ChunkKind.PARAGRAPH]
    diagnostics = result.pages[0].diagnostics
    assert [d.code for d in diagnostics] == ["html-skipped-content"]
    assert diagnostics[0].message.startswith("content inside <head>")


def test_html_script_content_is_skipped() -> None:
    backend = html_module.factory(_config("native-html"))
    html = "<script>var x = 1;</script><style>.a{}</style><p>real</p>"
    result = backend.convert(_request(html.encode()))
    assert _contents(result) == ["real"]
    codes = [d.code for d in result.pages[0].diagnostics]
    assert codes == ["html-skipped-content", "html-skipped-content"]


def test_html_nested_markup_text_joins_inline() -> None:
    backend = html_module.factory(_config("native-html"))
    result = backend.convert(_request(b"<p>Hello <b>world</b> and <code>span</code>.</p>"))
    assert _contents(result) == ["Hello world and span."]


def test_html_stray_text_becomes_paragraph() -> None:
    backend = html_module.factory(_config("native-html"))
    result = backend.convert(_request(b"<div>bare text</div>"))
    assert _kinds(result) == [ChunkKind.PARAGRAPH]
    assert _contents(result) == ["bare text"]


def test_html_image_counting_heuristic() -> None:
    backend = html_module.factory(_config("native-html"))
    analysis = backend.analyze(_source(b'<IMG src="a.png"><img src="b.png">'))
    assert analysis.signals[0].image_count == 2


def test_html_empty_heading_and_empty_list_diagnostics() -> None:
    backend = html_module.factory(_config("native-html"))
    result = backend.convert(_request(b"<h1></h1><ul></ul><ol></ol>"))
    assert result.pages[0].blocks == []
    codes = [d.code for d in result.pages[0].diagnostics]
    assert codes == ["html-empty-block", "html-empty-list", "html-empty-list"]


def test_html_empty_table_diagnostic() -> None:
    backend = html_module.factory(_config("native-html"))
    result = backend.convert(_request(b"<table></table>"))
    assert result.pages[0].blocks == []
    assert [d.code for d in result.pages[0].diagnostics] == ["html-empty-table"]


def test_html_unclosed_constructs_flush_on_finish() -> None:
    backend = html_module.factory(_config("native-html"))
    html = "<p>open paragraph<h2>open heading<ul><li>orphan item<table><tr><td>open cell"
    result = backend.convert(_request(html.encode()))
    kinds = _kinds(result)
    assert ChunkKind.PARAGRAPH in kinds
    assert ChunkKind.HEADING in kinds
    assert ChunkKind.LIST in kinds
    assert ChunkKind.TABLE in kinds
    table = next(b for b in result.pages[0].blocks if b.kind is ChunkKind.TABLE)
    assert table.content == "| open cell |\n| --- |"


def test_html_list_closed_without_li_finalizes_pending_item() -> None:
    backend = html_module.factory(_config("native-html"))
    result = backend.convert(_request(b"<ul><li>pending"))
    assert _kinds(result) == [ChunkKind.LIST]
    assert _contents(result) == ["pending"]


def test_html_heading_inside_blockquote_merges_into_quote() -> None:
    backend = html_module.factory(_config("native-html"))
    result = backend.convert(_request(b"<blockquote><h1>ignored heading</h1> kept text</blockquote>"))
    assert _kinds(result) == [ChunkKind.QUOTE]
    assert "kept text" in _contents(result)[0]


def test_html_paragraph_inside_list_item_stays_in_item() -> None:
    backend = html_module.factory(_config("native-html"))
    result = backend.convert(_request(b"<ul><li>item <p>with para</p></li></ul>"))
    assert _kinds(result) == [ChunkKind.LIST]
    assert _contents(result) == ["item with para"]


def test_html_pre_inside_list_item_stays_in_item() -> None:
    backend = html_module.factory(_config("native-html"))
    result = backend.convert(_request(b"<ul><li>item<pre>code here</pre></li></ul>"))
    assert _kinds(result) == [ChunkKind.LIST]
    assert _contents(result) == ["itemcode here"]


def test_html_code_language_from_pre_class() -> None:
    backend = html_module.factory(_config("native-html"))
    result = backend.convert(_request(b'<pre class="language-rust"><code>fn main() {}</code></pre>'))
    assert _kinds(result) == [ChunkKind.CODE]
    assert result.pages[0].blocks[0].metadata == {"language": "rust"}


def test_html_inline_code_without_pre_stays_paragraph() -> None:
    backend = html_module.factory(_config("native-html"))
    result = backend.convert(_request(b"<p>use <code>print()</code> here</p>"))
    assert _kinds(result) == [ChunkKind.PARAGRAPH]
    assert result.pages[0].blocks[0].metadata == {}


def test_html_stray_li_and_endtags_without_state_are_ignored() -> None:
    backend = html_module.factory(_config("native-html"))
    result = backend.convert(_request(b"<li>orphan</li></td></tr></table><p>ok</p>"))
    assert _contents(result) == ["orphan", "ok"]


def test_html_unclosed_cell_row_flushes_at_finish() -> None:
    backend = html_module.factory(_config("native-html"))
    result = backend.convert(_request(b"<table><tr><td>cell a"))
    assert _kinds(result) == [ChunkKind.TABLE]
    assert "cell a" in _contents(result)[0]


def test_html_finish_flushes_open_paragraph() -> None:
    backend = html_module.factory(_config("native-html"))
    result = backend.convert(_request(b"<p>at eof"))
    assert _kinds(result) == [ChunkKind.PARAGRAPH]
    assert _contents(result) == ["at eof"]


def test_html_finish_flushes_open_code_block() -> None:
    backend = html_module.factory(_config("native-html"))
    result = backend.convert(_request(b"<pre>trailing code at eof"))
    assert _kinds(result) == [ChunkKind.CODE]
    assert _contents(result) == ["trailing code at eof"]


def test_html_missing_close_tags_finalize_items_and_rows() -> None:
    backend = html_module.factory(_config("native-html"))
    result = backend.convert(_request(b"<ul><li>one<li>two</li></ul><table><tr><td>cell</table>"))
    assert _kinds(result) == [ChunkKind.LIST, ChunkKind.TABLE]
    assert _contents(result)[0] == "one\ntwo"
    assert "cell" in _contents(result)[1]
    closed_row = backend.convert(_request(b"<table><tr><td>row cell</tr></table>"))
    assert _kinds(closed_row) == [ChunkKind.TABLE]
    assert "row cell" in _contents(closed_row)[0]


# ── NativeBackendBase flow (cancellation, budget, timeout) ────────────────


def test_base_convert_keeps_pages_by_range() -> None:
    backend = _StubBackend(_config("native-stub"), text_module.factory.descriptor.capabilities, extract=lambda s, d: _pages(["a", "b", "c"]))
    request = ConversionRequest(
        source=_source(b"x"),
        page_range=PageRange(start=2, end=3),
    )
    result = backend.convert(request)
    assert [p.page_number for p in result.pages] == [2, 3]
    assert result.failures == []


def test_base_convert_cancellation_before_extraction() -> None:
    def explode(source: SourceDocument, data: bytes) -> list[Any]:
        raise AssertionError("extract must not run when cancelled")

    backend = _StubBackend(_config("native-stub"), text_module.factory.descriptor.capabilities, extract=explode)
    request = ConversionRequest(source=_source(b"x"), cancellation=lambda: True)
    result = backend.convert(request)
    assert result.pages == []
    assert [f.code for f in result.failures] == [FailureCode.CANCELLED]
    assert result.failures[0].detail == "cancelled before extraction"


def test_base_convert_cancellation_between_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("parsecraft.backends.native._common.monotonic", lambda: 0.0)
    calls: list[bool] = []

    def cancel_once() -> bool:
        calls.append(True)
        return len(calls) > 2  # allow start + first page

    backend = _StubBackend(_config("native-stub"), text_module.factory.descriptor.capabilities, extract=lambda s, d: _pages(["a", "b"]))
    result = backend.convert(ConversionRequest(source=_source(b"x"), cancellation=cancel_once))
    assert [p.page_number for p in result.pages] == [1]
    assert [f.code for f in result.failures] == [FailureCode.CANCELLED]
    assert result.failures[0].detail == "cancelled between pages"


def test_base_convert_budget_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("parsecraft.backends.native._common.monotonic", lambda: 0.0)
    backend = _StubBackend(_config("native-stub"), text_module.factory.descriptor.capabilities, extract=lambda s, d: _pages(["first page text", "second"]))
    request = ConversionRequest(source=_source(b"x"), max_output_chars=5)
    result = backend.convert(request)
    assert result.pages == []
    assert [f.code for f in result.failures] == [FailureCode.BUDGET_EXCEEDED]


def test_base_convert_budget_keeps_partial_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("parsecraft.backends.native._common.monotonic", lambda: 0.0)
    backend = _StubBackend(
        _config("native-stub"),
        text_module.factory.descriptor.capabilities,
        extract=lambda s, d: _pages(["tiny", "way too long for this budget"]),
    )
    request = ConversionRequest(source=_source(b"x"), max_output_chars=20)
    result = backend.convert(request)
    assert [p.page_number for p in result.pages] == [1]
    assert [f.code for f in result.failures] == [FailureCode.BUDGET_EXCEEDED]


def test_base_convert_timeout_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter([0.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr("parsecraft.backends.native._common.monotonic", lambda: next(times))
    backend = _StubBackend(_config("native-stub"), text_module.factory.descriptor.capabilities, extract=lambda s, d: _pages(["a"]))
    result = backend.convert(ConversionRequest(source=_source(b"x"), timeout_s=1.0))
    assert result.pages == []
    assert [f.code for f in result.failures] == [FailureCode.TIMEOUT]
    assert result.failures[0].budget_s == 1.0
    assert result.failures[0].elapsed_s == 100.0


def test_base_convert_dependency_failure_from_extract(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(source: SourceDocument, data: bytes) -> list[Any]:
        raise DependencyUnavailableError("some.module", "some-extra")

    backend = _StubBackend(_config("native-stub"), text_module.factory.descriptor.capabilities, extract=broken)
    result = backend.convert(ConversionRequest(source=_source(b"x")))
    assert result.pages == []
    assert [f.code for f in result.failures] == [FailureCode.DEPENDENCY_MISSING]
    assert "some.module" in result.failures[0].detail


def test_base_analyze_with_injected_counters() -> None:
    backend = _StubBackend(
        _config("native-stub"),
        text_module.factory.descriptor.capabilities,
        extract=lambda s, d: _pages(["a"]),
        page_count=lambda data: 42,
        count_images=lambda data: 7,
    )
    analysis = backend.analyze(_source(b"payload"))
    assert analysis.page_count == 42
    assert analysis.signals[0].image_count == 7


def _pages(texts: list[str]) -> list[PageResult]:
    return [PageResult(page_number=index, blocks=paragraph_chunks("stub", index, text)) for index, text in enumerate(texts, start=1)]


# ── registry interop + import hygiene ──────────────────────────────────────


def test_all_native_factories_register_and_create() -> None:
    """The native family now ships four backends here; pymupdf is a sibling package."""
    registry = BackendRegistry()
    expected = {"native-csv", "native-html", "native-markdown", "native-text", "pymupdf"}
    for module in (text_module, markdown_module, html_module, csv_module, pymupdf_module):
        registry.register(module.factory.descriptor.name, module.factory)
    descriptors = {d.name: d for d in registry.list_backends()}
    assert expected <= set(descriptors)
    for name in sorted(expected):
        assert descriptors[name].version == NATIVE_BACKEND_VERSION
        backend = registry.create(name, _config(name))
        assert backend.name == name


def test_native_family_imports_stay_offline_clean() -> None:
    # The subprocess must import THIS checkout: the ambient interpreter
    # resolves parsecraft to the shared checkout, which lacks new modules.
    src_dir = Path(__file__).resolve().parents[2] / "src"
    env = {**os.environ, "PYTHONPATH": str(src_dir)}
    script = """
import sys

import parsecraft.adapters
import parsecraft.backends.native.csv
import parsecraft.backends.native.html
import parsecraft.backends.native.markdown
import parsecraft.backends.native.text

heavy = [
    m
    for m in (
        "pymupdf",
        "pypdf",
        "httpx",
        "charset_normalizer",
        "huggingface_hub",
        "torch",
        "transformers",
        "vllm",
        "docling",
    )
    if m in sys.modules
]
assert not heavy, f"heavy modules imported: {heavy}"
print("NATIVE_IMPORTS_OK")
"""
    proc = subprocess.run(  # noqa: S603 - trusted local interpreter, fixed argv, no shell
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "NATIVE_IMPORTS_OK" in proc.stdout


# ── structured table rows (pc-4u7.40) ──────────────────────────────────────


def test_html_table_carries_structured_rows() -> None:
    backend = html_module.factory(_config("native-html"))
    html = "<table><tr><th>id</th><th>name</th></tr><tr><td>1</td><td>ada</td></tr></table>"
    result = backend.convert(_request(html.encode("utf-8")))
    table = result.pages[0].blocks[0]
    assert table.kind is ChunkKind.TABLE
    assert table.rows == (("id", "name"), ("1", "ada"))
    # round-trip: rows are exactly the content cells (separator row excluded)
    content_rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in table.content.splitlines() if "---" not in line]
    assert [list(row) for row in table.rows] == content_rows


def test_html_non_table_chunks_carry_no_rows() -> None:
    backend = html_module.factory(_config("native-html"))
    result = backend.convert(_request(b"<h1>Head</h1><p>Body.</p>"))
    for block in result.pages[0].blocks:
        assert block.rows is None


def test_csv_descriptor_claims_both_delimited_types() -> None:
    assert csv_module.BACKEND_NAME == "native-csv"
    assert csv_module.SUPPORTED_FORMATS == ["text/csv", "text/tab-separated-values"]
    descriptor = csv_module.factory.descriptor
    # Parser and projection changes must invalidate earlier cached documents.
    assert descriptor.version == NATIVE_BACKEND_VERSION == "0.1.2"
    assert descriptor.capabilities.supports_page_ranges is False
    assert descriptor.capabilities.supports_multi_page is False
    backend = csv_module.factory(_config("native-csv"))
    assert backend.name == "native-csv"


def test_csv_basic_table_rows_and_content_agree() -> None:
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(csv_bytes(), uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.kind is ChunkKind.TABLE
    assert table.rows == (
        ("id", "name", "note"),
        ("1", "alpha", "first"),
        ("2", "beta, jr", "second"),
        ("3", "gamma", "last"),
    )
    assert table.content == ("| id | name | note |\n| --- | --- | --- |\n| 1 | alpha | first |\n| 2 | beta, jr | second |\n| 3 | gamma | last |")
    # agreement: every grid cell reaches the projection from the same source
    for row in table.rows:
        for cell in row:
            assert cell in table.content
    assert table.metadata == {"backend": "native-csv", "delimiter": ","}
    assert result.pages[0].diagnostics == []


def test_csv_header_is_row_zero_never_metadata() -> None:
    """Consumer contract (kompend-1): ``rows`` carries the header as row 0.

    Lifting the header into metadata would silently turn the consumer's column
    names into the first data row — no error, wrong table. There is no caption
    key either: a CSV has no caption to report.
    """
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(csv_bytes(), uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.rows is not None
    assert table.rows[0] == ("id", "name", "note")
    assert set(table.metadata) == {"backend", "delimiter"}
    assert "caption" not in table.metadata


def test_csv_tsv_forces_tab_by_suffix() -> None:
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(tsv_bytes(), uri="sample.tsv"))
    (table,) = result.pages[0].blocks
    assert table.kind is ChunkKind.TABLE
    assert table.rows == (
        ("id", "name", "score"),
        ("1", "alpha", "0.5"),
        ("2", "beta", "0.75"),
        ("3", "gamma\tdelta", "1.0"),
    )
    assert table.metadata == {"backend": "native-csv", "delimiter": "\t"}
    assert result.pages[0].diagnostics == []


def test_csv_tsv_media_type_beats_suffix() -> None:
    backend = csv_module.factory(_config("native-csv"))
    source = SourceDocument(uri="sample.csv", media_type="text/tab-separated-values", content=b"a\tb\n1\t2\n")
    (table,) = backend.convert(ConversionRequest(source=source)).pages[0].blocks
    assert table.rows == (("a", "b"), ("1", "2"))
    assert table.metadata["delimiter"] == "\t"
    source = SourceDocument(uri="sample.tsv", media_type="text/csv", content=b"a,b\n1,2\n")
    result = backend.convert(ConversionRequest(source=source))
    (table,) = result.pages[0].blocks
    assert table.rows == (("a", "b"), ("1", "2"))
    assert table.metadata["delimiter"] == ","
    assert result.pages[0].diagnostics == []


def test_csv_semicolon_sniff() -> None:
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(b"a;b\n1;2\n3;4\n", uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.rows == (("a", "b"), ("1", "2"), ("3", "4"))
    assert table.metadata["delimiter"] == ";"
    assert result.pages[0].diagnostics == []


def test_csv_pipe_sniff_with_ragged_tail() -> None:
    backend = csv_module.factory(_config("native-csv"))
    content = b"a|b\n1|2\n3|4\n5|6\n7|8\n9|10|11\n"
    result = backend.convert(_csv_request(content, uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.metadata["delimiter"] == "|"
    assert table.rows == (("a", "b"), ("1", "2"), ("3", "4"), ("5", "6"), ("7", "8"), ("9", "10", "11"))
    # ragged rows are kept; the projection pads display-only to max width
    assert table.content == ("| a | b |  |\n| --- | --- | --- |\n| 1 | 2 |  |\n| 3 | 4 |  |\n| 5 | 6 |  |\n| 7 | 8 |  |\n| 9 | 10 | 11 |")
    assert result.pages[0].diagnostics == []


def test_csv_sniff_tie_prefers_comma() -> None:
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(b"a,b;c\n1,2;3\n", uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.metadata["delimiter"] == ","
    assert table.rows == (("a", "b;c"), ("1", "2;3"))
    assert result.pages[0].diagnostics == []


def test_csv_sniff_fallback_prose_degrades_to_single_column_table() -> None:
    """A file that merely ends in ``.csv`` ingests as a table, never raises."""
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(b"Just some notes\nabout things\nno structure here\n", uri="notes.csv"))
    (table,) = result.pages[0].blocks
    assert table.kind is ChunkKind.TABLE
    assert table.rows == (("Just some notes",), ("about things",), ("no structure here",))
    assert table.metadata["delimiter"] == ","
    (diagnostic,) = result.pages[0].diagnostics
    assert diagnostic.level is DiagnosticLevel.INFO
    assert diagnostic.code == "csv-sniff-fallback"


def test_csv_embedded_newline_survives_sniff_and_parse() -> None:
    """A quoted newline remains within one cell, without a fallback diagnostic."""
    backend = csv_module.factory(_config("native-csv"))
    content = b'id,name,note\r\n1,alpha,first\r\n2,"beta, jr","line one\nline two"\r\n3,gamma,last\r\n'
    result = backend.convert(_csv_request(content, uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.metadata["delimiter"] == ","
    assert result.pages[0].diagnostics == []
    assert table.rows == (
        ("id", "name", "note"),
        ("1", "alpha", "first"),
        ("2", "beta, jr", "line one\nline two"),
        ("3", "gamma", "last"),
    )
    # Projection escapes (pc-9p6.13): the raw newline must not reach the table
    # lines — one logical row stays one physical line, rows stay literal.
    assert "line one\nline two" not in table.content
    assert "line one<br />line two" in table.content
    assert table.content.count("\n") == 4


@pytest.mark.parametrize(
    ("content", "delimiter", "expected"),
    [
        (
            b'id,description\n1,"alpha\nx|y\nz|w\nfinish"\n2,end\n',
            ",",
            (("id", "description"), ("1", "alpha\nx|y\nz|w\nfinish"), ("2", "end")),
        ),
        (
            b'id;description\n1;"alpha\nx,y\nz,w\nfinish"\n2;end\n',
            ";",
            (("id", "description"), ("1", "alpha\nx,y\nz,w\nfinish"), ("2", "end")),
        ),
        (
            b'id,"description|alias"\n1,"alpha\nx|y\nz|w\nq|r\nfinish"\n2,end\n',
            ",",
            (("id", "description|alias"), ("1", "alpha\nx|y\nz|w\nq|r\nfinish"), ("2", "end")),
        ),
        (
            b'id,name,note\n1,Alice,"red;""urgent"";blue"\n2,Bob,green\n3,Eve,black\n',
            ",",
            (("id", "name", "note"), ("1", "Alice", 'red;"urgent";blue'), ("2", "Bob", "green"), ("3", "Eve", "black")),
        ),
        (
            b'id,notes\n1,"2025-01-01, 10:00; started\n2025-01-02, 10:00; retry\n2025-01-03, 10:00; failed\n2025-01-04, 10:00; stopped"\n',
            ",",
            (("id", "notes"), ("1", "2025-01-01, 10:00; started\n2025-01-02, 10:00; retry\n2025-01-03, 10:00; failed\n2025-01-04, 10:00; stopped")),
        ),
    ],
)
def test_csv_multiline_fields_do_not_vote_for_embedded_delimiters(content: bytes, delimiter: str, expected: tuple[tuple[str, ...], ...]) -> None:
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(content, uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.metadata["delimiter"] == delimiter
    assert table.rows == expected


def test_csv_sniff_does_not_treat_literal_quotes_as_competing_fields() -> None:
    content = b'h,foo;"x,y"z;extra;more\n1,bar;"a,b"z;extra;more\n'
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(content, uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.metadata["delimiter"] == ","
    assert table.rows == (("h", 'foo;"x', 'y"z;extra;more'), ("1", 'bar;"a', 'b"z;extra;more'))


def test_csv_sniff_skips_candidate_exceeding_field_limit() -> None:
    field = "x" * 70_000
    content = f"left,right\n{field},{field}\n".encode()
    previous_limit = csv.field_size_limit(131_072)
    try:
        backend = csv_module.factory(_config("native-csv"))
        result = backend.convert(_csv_request(content, uri="sample.csv"))
    finally:
        csv.field_size_limit(previous_limit)
    (table,) = result.pages[0].blocks
    assert table.metadata["delimiter"] == ","
    assert table.rows == (("left", "right"), (field, field))


def test_csv_sniff_does_not_count_truncated_multiline_cell_as_rows() -> None:
    multiline = "start\n" + ("log|" + "x" * 700 + "\n") * 100 + "finish"
    text = f'id,note\n1,ok\n2,ok\n3,"{multiline}"\n4,done\n'
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(text.encode(), uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.metadata["delimiter"] == ","
    assert table.rows == (("id", "note"), ("1", "ok"), ("2", "ok"), ("3", multiline), ("4", "done"))


def test_csv_detects_delimiter_from_explicit_empty_fields() -> None:
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(b";;\n;;\n", uri="empty-fields.csv"))
    (table,) = result.pages[0].blocks
    assert table.metadata["delimiter"] == ";"
    assert table.rows == (("", "", ""), ("", "", ""))


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"report, status\na;b\n1;2\n3;4\n", (("report, status",), ("a", "b"), ("1", "2"), ("3", "4"))),
        (b'title\n"oops" ;bar\n1;2\n3;4\n', (("title",), ("oops ", "bar"), ("1", "2"), ("3", "4"))),
    ],
)
def test_csv_sniff_uses_later_rows_after_ragged_or_malformed_header(content: bytes, expected: tuple[tuple[str, ...], ...]) -> None:
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(content, uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.metadata["delimiter"] == ";"
    assert table.rows == expected


def test_csv_ragged_semicolon_still_detected() -> None:
    """One ragged row must not collapse a ``;`` file to a single column."""
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(b"a;b;c\n1;2\n", uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.metadata["delimiter"] == ";"
    assert table.rows == (("a", "b", "c"), ("1", "2"))
    assert result.pages[0].diagnostics == []


def test_csv_ragged_pipe_still_detected() -> None:
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(b"a|b|c\n1|2\n", uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.metadata["delimiter"] == "|"
    assert table.rows == (("a", "b", "c"), ("1", "2"))
    assert result.pages[0].diagnostics == []


def test_csv_ragged_tail_semicolon_keeps_columns() -> None:
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(b"a;b;c\n1;2;3\n4;5\n", uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.metadata["delimiter"] == ";"
    assert table.rows == (("a", "b", "c"), ("1", "2", "3"), ("4", "5"))
    assert result.pages[0].diagnostics == []


def test_csv_malformed_quote_probe_never_raises() -> None:
    """An unterminated quote degrades through the lenient parser."""
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(b'a;b\n"oops\n1;2\n', uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.metadata["delimiter"] == ";"
    assert table.rows == (("a", "b"), ("oops\n1;2\n",))
    assert result.pages[0].diagnostics == []


def test_csv_crlf_and_bom_tolerated() -> None:
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(b"\xef\xbb\xbfa,b\r\n1,2\r\n", uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.rows == (("a", "b"), ("1", "2"))
    assert result.pages[0].diagnostics == []


def test_csv_tsv_bom_stripped_without_consumer_normalization() -> None:
    """The consumer UTF-8-normalizes ``.csv`` but NOT ``.tsv`` — the backend
    strips the BOM itself on both paths.
    """
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(b"\xef\xbb\xbfid\tname\n1\tx\n", uri="sample.tsv"))
    (table,) = result.pages[0].blocks
    assert table.rows == (("id", "name"), ("1", "x"))


def test_csv_utf16_bom_decodes_to_real_rows() -> None:
    """Excel UTF-16 exports must not parse as NUL-interleaved junk rows."""
    backend = csv_module.factory(_config("native-csv"))
    content = "name,level\r\nC/I,3\r\n".encode("utf-16")
    result = backend.convert(_csv_request(content, uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.rows == (("name", "level"), ("C/I", "3"))
    assert result.pages[0].diagnostics == []


def test_csv_long_field_survives_field_limit() -> None:
    """A 200 KB cell exceeds the stdlib default cap — extraction keeps it."""
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(b"a,b\n" + b"x" * 200_000 + b",2\n", uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.rows == (("a", "b"), ("x" * 200_000, "2"))


def test_csv_empty_file_emits_no_chunk() -> None:
    """An empty grid emits no chunk: a content-less TABLE would surface
    downstream as an empty table element. The page stays diagnostic-only.
    """
    backend = csv_module.factory(_config("native-csv"))
    for content in (b"", b"\n\n\n"):
        result = backend.convert(_csv_request(content, uri="sample.csv"))
        assert result.pages[0].blocks == []
        codes = [diagnostic.code for diagnostic in result.pages[0].diagnostics]
        assert codes == ["csv-sniff-fallback", "csv-empty-table"]
        assert result.pages[0].diagnostics[0].level is DiagnosticLevel.INFO
    result = backend.convert(_csv_request(b"", uri="sample.tsv"))
    assert result.pages[0].blocks == []
    assert [diagnostic.code for diagnostic in result.pages[0].diagnostics] == ["csv-empty-table"]


def test_csv_trailing_blank_rows_trimmed_interior_kept() -> None:
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(b"a,b\n1,2\n\n3,4\n   \n\n", uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.rows == (("a", "b"), ("1", "2"), (), ("3", "4"), ("   ",))
    assert table.content == "| a | b |\n| --- | --- |\n| 1 | 2 |\n|  |  |\n| 3 | 4 |\n|     |  |"


@pytest.mark.parametrize(
    ("content", "last_row"),
    [
        (b"a,b\n,\n", ("", "")),
        (b'a,b\n""\n', ("",)),
        (b'a,b\n" "\n', (" ",)),
        (b"a,b\n   \n", ("   ",)),
    ],
)
def test_csv_preserves_explicit_empty_or_whitespace_cells_at_end(content: bytes, last_row: tuple[str, ...]) -> None:
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(content, uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.rows == (("a", "b"), last_row)


def test_csv_markdown_table_renders_empty_grid_empty() -> None:
    """The projection is total: an empty grid renders empty, never raises."""
    assert csv_module._markdown_table(()) == ""


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("plain", "plain"),
        ("", ""),
        ("x|y", "x\\|y"),
        ("a\\b", "a\\\\b"),
        ("a\\|b", "a\\\\\\|b"),
        ("x\ny", "x<br />y"),
        ("x\r\ny", "x<br />y"),
        ("x\ry", "x<br />y"),
        ("AT&T", "AT&amp;T"),
        ("a<b", "a&lt;b"),
        ("a>b", "a&gt;b"),
        ("&amp;", "&amp;amp;"),
        ("a<br />b", "a&lt;br /&gt;b"),
    ],
)
def test_csv_escape_cell_unit(cell: str, expected: str) -> None:
    """Pipe/backslash/newline escaping; identity on plain cells."""
    assert csv_module._escape_cell(cell) == expected


def test_csv_pipe_cell_escaped_rows_literal() -> None:
    """A ``|`` cell is escaped in content; rows keep the literal pipe."""
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(b'a,b\n"x|y",2\n', uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.rows == (("a", "b"), ("x|y", "2"))
    assert table.content == "| a | b |\n| --- | --- |\n| x\\|y | 2 |"


def test_csv_backslash_cannot_smuggle_column_break() -> None:
    r"""Backslashes escape first, so a literal ``a\|b`` stays one cell."""
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(b"a,b\na\\|b,2\n", uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.rows == (("a", "b"), ("a\\|b", "2"))
    assert table.content == "| a | b |\n| --- | --- |\n| a\\\\\\|b | 2 |"


def test_csv_literal_markup_escaped_not_reinterpreted() -> None:
    """Literal ``<br />``/``<b>`` become entities; only real breaks use ``<br />``.

    Entity escaping runs before break insertion, so a literal break marker
    renders as visible text — never as a break, never confused with one.
    """
    backend = csv_module.factory(_config("native-csv"))
    content = b'a,b\n"a<br />b","<b>x|y</b>"\n'
    result = backend.convert(_csv_request(content, uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.rows == (("a", "b"), ("a<br />b", "<b>x|y</b>"))
    assert table.content == ("| a | b |\n| --- | --- |\n| a&lt;br /&gt;b | &lt;b&gt;x\\|y&lt;/b&gt; |")


@pytest.mark.parametrize(
    ("raw", "cell"),
    [
        (b'a,b\n"x\ny",2\n', "x\ny"),
        (b'a,b\n"x\r\ny",2\n', "x\r\ny"),
        (b'a,b\n"x\ry",2\n', "x\ry"),
    ],
)
def test_csv_all_newline_flavours_stay_on_one_physical_line(raw: bytes, cell: str) -> None:
    """LF, CRLF, and lone CR all become ``<br />``; rows keep the real break."""
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(raw, uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.rows == (("a", "b"), (cell, "2"))
    assert table.content == "| a | b |\n| --- | --- |\n| x<br />y | 2 |"


def test_csv_projection_renders_distinct_breaks_in_markdown_it() -> None:
    """Browser-facing: literal markup escaped, real breaks break (pc-9p6.13)."""
    backend = csv_module.factory(_config("native-csv"))
    content = b'h1,h2\n"a<br />b","x\ny"\n"<b>t</b>","&amp;"\n'
    result = backend.convert(_csv_request(content, uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.rows == (("h1", "h2"), ("a<br />b", "x\ny"), ("<b>t</b>", "&amp;"))
    rendered = MarkdownIt().enable("table").render(table.content)
    assert "<td>a&lt;br /&gt;b</td>" in rendered
    assert "<td>x<br />y</td>" in rendered
    assert "<td>&lt;b&gt;t&lt;/b&gt;</td>" in rendered
    assert "<td>&amp;amp;</td>" in rendered
    assert rendered.count("<td") == 4


def test_csv_multiline_cell_stays_on_one_physical_line() -> None:
    """A quoted newline becomes ``<br />``; rows keep the real newline."""
    backend = csv_module.factory(_config("native-csv"))
    result = backend.convert(_csv_request(b'a,b\n"x\ny",2\n', uri="sample.csv"))
    (table,) = result.pages[0].blocks
    assert table.rows == (("a", "b"), ("x\ny", "2"))
    assert table.content == "| a | b |\n| --- | --- |\n| x<br />y | 2 |"


# ── native-csv: delimited data → TABLE (pc-oir.1) ──────────────────────────


def _csv_request(content: bytes, uri: str) -> ConversionRequest:
    return ConversionRequest(source=_source(content, uri=uri))


def test_csv_analyze_marks_text_bearing() -> None:
    backend = csv_module.factory(_config("native-csv"))
    analysis = backend.analyze(_source(csv_bytes(), uri="sample.csv"))
    assert analysis.page_count == 1
    assert analysis.signals[0].has_native_text is True
    assert analysis.signals[0].text_chars == len(csv_bytes())
    assert analysis.signals[0].image_count == 0


def test_csv_convert_from_disk(tmp_path: Path) -> None:
    backend = csv_module.factory(_config("native-csv"))
    path = tmp_path / "sample.csv"
    path.write_bytes(csv_bytes())
    result = backend.convert(ConversionRequest(source=SourceDocument(uri=str(path))))
    (table,) = result.pages[0].blocks
    assert table.rows is not None
    assert table.rows[0] == ("id", "name", "note")


def _kinds(result: Any, page: int = 1) -> list[ChunkKind]:
    return [block.kind for block in result.pages[page - 1].blocks]


def _request(content: bytes, **kwargs: Any) -> ConversionRequest:
    return ConversionRequest(source=_source(content), **kwargs)


def _source(content: bytes, uri: str = "mem://native") -> SourceDocument:
    return SourceDocument(uri=uri, content=content)


def _config(name: str) -> BackendConfig:
    return BackendConfig(name=name)


def _contents(result: Any, page: int = 1) -> list[str]:
    return [block.content for block in result.pages[page - 1].blocks]
