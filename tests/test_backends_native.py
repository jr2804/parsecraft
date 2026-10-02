"""Offline tests for the native backend family (text/markdown/html/pdf)."""

from __future__ import annotations

import hashlib
import importlib
import io
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from parsecraft.backends.native import html as html_module
from parsecraft.backends.native import markdown as markdown_module
from parsecraft.backends.native import pdf as pdf_module
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
from parsecraft.backends.native.code_layout import PageText, page_text
from parsecraft.backends.protocol import (
    BackendConfig,
    ConversionRequest,
    SourceDocument,
)
from parsecraft.backends.registry import BackendRegistry
from parsecraft.ir.models import ChunkKind, FailureCode, PageRange, PageResult, PassKind
from tests.fixtures.documents import document_bytes

_PDF_TEXT_MODULE = "parsecraft.backends.native.pdf_text"


# ── NativeBackendBase flow (via a stub backend) ────────────────────────────


class _StubBackend(NativeBackendBase):
    """Minimal concrete subclass for exercising the shared flow."""

    name = "native-stub"


# ── pdf_inspect coverage without the pdf-lite extra (fake pypdf) ───────────


class _FakePasswordType:
    NOT_DECRYPTED = 0
    USER_PASSWORD = 1


class _FakePdfPage:
    def __init__(self, text: str | None) -> None:
        self._text = text

    def extract_text(self) -> str | None:
        return self._text


class _FakePdfReader:
    def __init__(self, *, encrypted: bool, decrypt_result: int, texts: list[str | None]) -> None:
        self.is_encrypted = encrypted
        self._decrypt_result = decrypt_result
        self.pages = [_FakePdfPage(text) for text in texts]

    def decrypt(self, password: str) -> int:
        return self._decrypt_result


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


def _kinds(result: Any, page: int = 1) -> list[ChunkKind]:
    return [block.kind for block in result.pages[page - 1].blocks]


def test_pdf_descriptor_contract() -> None:
    descriptor = pdf_module.factory.descriptor
    assert descriptor.name == "native-pdf"
    assert descriptor.version == NATIVE_BACKEND_VERSION
    assert descriptor.capabilities.supported_formats == ["application/pdf"]
    assert descriptor.capabilities.supports_page_ranges is True
    assert descriptor.capabilities.supports_multi_page is True
    assert descriptor.capabilities.optional_dependency_group == "pdf-lite"


def test_pdf_factory_and_analyze_with_real_pypdf() -> None:
    pytest.importorskip("pypdf", reason="pdf-lite extra absent in the light env")
    backend = pdf_module.factory(_config("native-pdf"))
    data = document_bytes("pdf", pages=[["page one text"], ["page two text", "more"]])
    analysis = backend.analyze(_source(data))
    assert analysis.page_count == 2
    assert [s.page_number for s in analysis.signals] == [1, 2]
    assert all(s.text_chars > 0 and not s.blank for s in analysis.signals)
    assert analysis.diagnostics == []


def test_pdf_analyze_encrypted_pdf_reports_warning() -> None:
    backend = pdf_module.factory(_config("native-pdf"))
    data = _encrypted_pdf(document_bytes("pdf"))
    analysis = backend.analyze(_source(data))
    assert analysis.page_count == 0
    assert analysis.signals == []
    assert [d.code for d in analysis.diagnostics] == ["pdf-encrypted"]
    assert analysis.diagnostics[0].message.endswith("without a password")


# ── native-pdf ─────────────────────────────────────────────────────────────


def _encrypted_pdf(data: bytes) -> bytes:
    pypdf = pytest.importorskip("pypdf", reason="pdf-lite extra absent in the light env")
    writer = pypdf.PdfWriter()
    writer.append(io.BytesIO(data))
    writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_pdf_analyze_blank_page_reports_no_extractable_text() -> None:
    pytest.importorskip("pypdf", reason="pdf-lite extra absent in the light env")
    backend = pdf_module.factory(_config("native-pdf"))
    data = document_bytes("pdf", pages=[[]])
    analysis = backend.analyze(_source(data))
    assert analysis.page_count == 1
    assert analysis.signals[0].blank is True
    assert analysis.signals[0].replacement_char_ratio is None
    assert [d.code for d in analysis.diagnostics] == ["pdf-no-extractable-text"]


def test_pdf_factory_raises_when_inspect_module_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def raiser(name: str) -> ModuleType:
        if name.endswith("pdf_inspect"):
            raise ImportError(name)
        return importlib.import_module(name)

    monkeypatch.setattr("parsecraft.backends.native.pdf.import_module", raiser)
    with pytest.raises(DependencyUnavailableError, match="pdf-lite"):
        pdf_module.factory(_config("native-pdf"))


def test_pdf_convert_missing_pymupdf_yields_typed_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def raiser(name: str) -> ModuleType:
        if name == _PDF_TEXT_MODULE:
            raise ImportError(name)
        return importlib.import_module(name)

    monkeypatch.setattr("parsecraft.backends.native.pdf.import_module", raiser)
    backend = pdf_module.factory(_config("native-pdf"))
    data = document_bytes("pdf")
    result = backend.convert(_request(data))
    assert result.pages == []
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.code is FailureCode.DEPENDENCY_MISSING
    assert failure.pass_kind is PassKind.NATIVE
    assert failure.backend == "native-pdf"
    assert "pdf" in failure.detail


def test_pdf_convert_with_fake_extraction_impl(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeImpl:
        @staticmethod
        def page_layouts(data: bytes) -> list[PageText]:
            # Two pages of prose: one line, then a blank-line-separated pair.
            return [_page("first page block"), _page("second page", "", "line two")]

    def loader(name: str) -> Any:
        if name == _PDF_TEXT_MODULE:
            return FakeImpl()
        return importlib.import_module(name)

    monkeypatch.setattr("parsecraft.backends.native.pdf.import_module", loader)
    backend = pdf_module.factory(_config("native-pdf"))
    result = backend.convert(_request(document_bytes("pdf")))
    assert len(result.pages) == 2
    assert _contents(result, page=1) == ["first page block"]
    assert _contents(result, page=2) == ["second page", "line two"]
    assert [p.page_number for p in result.pages] == [1, 2]
    assert result.failures == []


def _page(*texts: str) -> PageText:
    """A page of 12 pt single-span lines; an empty entry leaves a blank line.

    Blank lines carry no glyphs, so the text layer shows them only as extra
    vertical space — which is exactly what this helper models.
    """
    lines: list[dict[str, object]] = []
    y = 10.0
    for text in texts:
        if not text:
            y += 24.0  # one blank line of vertical space
            continue
        lines.append(
            {
                "bbox": (0.0, y, 100.0, y + 12.0),
                "spans": [{"font": "Helvetica", "size": 12.0, "bbox": (0.0, y, 100.0, y + 12.0), "text": text}],
            }
        )
        y += 12.0
    return page_text({"blocks": [{"type": 0, "lines": lines}]})


def _contents(result: Any, page: int = 1) -> list[str]:
    return [block.content for block in result.pages[page - 1].blocks]


def test_pdf_text_impl_module_loads_with_fake_pymupdf(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []

    class FakePage:
        def __init__(self, text: str) -> None:
            self._text = text

        def get_text(self, kind: str) -> dict[str, object]:
            assert kind == "dict"  # extraction hands over geometry, not plain text
            return {
                "blocks": [
                    {
                        "type": 0,
                        "lines": [
                            {
                                "bbox": (0.0, 0.0, 40.0, 12.0),
                                "spans": [{"font": "Helvetica", "size": 12.0, "bbox": (0.0, 0.0, 40.0, 12.0), "text": self._text}],
                            }
                        ],
                    }
                ]
            }

    class FakeDocument:
        def __init__(self) -> None:
            self._pages = [FakePage("alpha"), FakePage("beta")]

        def __iter__(self) -> Iterator[FakePage]:
            return iter(self._pages)

        @staticmethod
        def close() -> None:
            closed.append(True)

    class FakePymupdf:
        @staticmethod
        def open(*, stream: bytes, filetype: str) -> FakeDocument:
            assert stream.startswith(b"%PDF")
            assert filetype == "pdf"
            return FakeDocument()

    monkeypatch.setitem(sys.modules, "pymupdf", FakePymupdf)
    monkeypatch.delitem(sys.modules, "parsecraft.backends.native.pdf_text", raising=False)
    impl = cast(Any, importlib.import_module("parsecraft.backends.native.pdf_text"))
    layouts = impl.page_layouts(b"%PDF-fake")
    assert [line.text for page in layouts for line in page.lines] == ["alpha", "beta"]
    assert closed == [True]


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
    registry = BackendRegistry()
    expected = {"native-html", "native-markdown", "native-pdf", "native-text"}
    for module in (text_module, markdown_module, html_module, pdf_module):
        registry.register(module.factory.descriptor.name, module.factory)
    descriptors = {d.name: d for d in registry.list_backends()}
    assert expected <= set(descriptors)
    for name in sorted(expected):
        assert descriptors[name].version == NATIVE_BACKEND_VERSION
        backend = registry.create(name, _config(name))
        assert backend.name == name


def test_native_family_imports_stay_offline_clean() -> None:
    script = """
import sys

import parsecraft.adapters
import parsecraft.backends.native.html
import parsecraft.backends.native.markdown
import parsecraft.backends.native.pdf
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
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "NATIVE_IMPORTS_OK" in proc.stdout


def test_pdf_inspect_statistics_against_fake_pypdf(monkeypatch: pytest.MonkeyPatch) -> None:
    # plain PDF: text, blank page, garbled page, and an extract_text() None
    reader = _FakePdfReader(encrypted=False, decrypt_result=0, texts=["Hello world.", "", "bad � data", None])
    module = _import_pdf_inspect(monkeypatch, reader)
    report = module.inspect(b"%PDF-fake")
    assert report.encrypted is False
    assert report.readable is True
    assert report.page_count == 4
    assert [stats.blank for stats in report.pages] == [False, True, False, True]
    assert report.pages[0].replacement_char_ratio == 0.0
    assert report.pages[1].replacement_char_ratio is None
    assert report.pages[2].replacement_char_ratio is not None
    assert report.pages[2].replacement_char_ratio > 0
    assert report.pages[3].replacement_char_ratio is None  # extract_text() -> None


def test_pdf_inspect_encrypted_paths_against_fake_pypdf(monkeypatch: pytest.MonkeyPatch) -> None:
    # encrypted, empty password rejected → unreadable, no page stats
    blocked = _FakePdfReader(encrypted=True, decrypt_result=_FakePasswordType.NOT_DECRYPTED, texts=[])
    report = _import_pdf_inspect(monkeypatch, blocked).inspect(b"%PDF-fake")
    assert report.encrypted is True
    assert report.readable is False
    assert report.page_count == 0
    assert report.pages == []

    # encrypted, empty password accepted → readable with stats
    openable = _FakePdfReader(encrypted=True, decrypt_result=_FakePasswordType.USER_PASSWORD, texts=["secret text"])
    report = _import_pdf_inspect(monkeypatch, openable).inspect(b"%PDF-fake")
    assert report.encrypted is True
    assert report.readable is True
    assert report.page_count == 1
    assert report.pages[0].text_chars == len("secret text")


def _import_pdf_inspect(monkeypatch: pytest.MonkeyPatch, reader: _FakePdfReader) -> Any:
    fake = ModuleType("pypdf")
    fake.PasswordType = _FakePasswordType  # ty: ignore[unresolved-attribute] — fake module
    fake.PdfReader = lambda stream: reader  # ty: ignore[unresolved-attribute] — fake module
    monkeypatch.setitem(sys.modules, "pypdf", fake)
    monkeypatch.delitem(sys.modules, "parsecraft.backends.native.pdf_inspect", raising=False)
    return importlib.import_module("parsecraft.backends.native.pdf_inspect")


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


def _request(content: bytes, **kwargs: Any) -> ConversionRequest:
    return ConversionRequest(source=_source(content), **kwargs)


def _source(content: bytes, uri: str = "mem://native") -> SourceDocument:
    return SourceDocument(uri=uri, content=content)


def _config(name: str) -> BackendConfig:
    return BackendConfig(name=name)
