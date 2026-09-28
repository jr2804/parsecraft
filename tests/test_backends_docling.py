"""Docling backend: light/heavy boundary, verified formats, bound-checked convert.

Fully offline: the heavy ``docling`` / ``docling_core`` / ``pypdfium2`` imports
are satisfied by stub modules in ``sys.modules`` before ``_impl`` loads — the
same pattern as the LiteParse and OCR families.
"""

from __future__ import annotations

import hashlib
import importlib
import re
import sys
import time
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

from parsecraft.backends.docling import docling as docling_module
from parsecraft.backends.errors import BackendError, DependencyUnavailableError
from parsecraft.backends.protocol import BackendConfig, BackendResult, ConversionRequest, DocumentBackend, SourceDocument
from parsecraft.ir.models import ChunkKind, FailureCode, PageRange, PassKind

_PDF_BYTES = b"%PDF-1.7 stub"
_PDF_SOURCE = SourceDocument(uri="file:///doc.pdf", media_type="application/pdf", content=_PDF_BYTES)


# ── Stubs: heavy packages faked through sys.modules ─────────────────────────────


class _Prov:
    def __init__(self, page_no: int) -> None:
        self.page_no = page_no


class _TextItem:
    def __init__(self, text: str, label: str = "text", page_no: int | None = 1) -> None:
        self.text = text
        self.label = label
        self.prov = [] if page_no is None else [_Prov(page_no)]


class _TableItem:
    def __init__(self, markdown: str, page_no: int = 1, rows: list[list[types.SimpleNamespace]] | None = None) -> None:
        self._markdown = markdown
        self.prov = [_Prov(page_no)]
        self.last_doc: object = None
        # Mirrors the real accessor: TableItem.data.grid -> list[list[TableCell]]
        self.data = types.SimpleNamespace(grid=rows if rows is not None else _grid_from_markdown(markdown))

    def export_to_markdown(self, doc: object = None) -> str:
        self.last_doc = doc
        return self._markdown


class _OtherItem:
    """A non-text, non-table item (picture/group stand-in)."""

    def __init__(self, page_no: int = 1) -> None:
        self.prov = [_Prov(page_no)]


class _Document:
    def __init__(self, items: list[object]) -> None:
        self._items = items

    def iterate_items(self) -> Iterator[tuple[object, int]]:
        for item in self._items:
            yield item, 0


class _ConversionResult:
    def __init__(self, items: list[object]) -> None:
        self.document = _Document(items)


class _Converter:
    def __init__(self, state: _State) -> None:
        self._state = state

    def convert(self, source: object, page_range: tuple[int, int] | None = None) -> _ConversionResult:
        self._state.convert_calls.append((getattr(source, "name", None), page_range))
        if self._state.convert_error is not None:
            raise self._state.convert_error
        if self._state.delay_s > 0:
            time.sleep(self._state.delay_s)
        return _ConversionResult(list(self._state.items))


class _DocumentStream:
    def __init__(self, name: str, stream: object) -> None:
        self.name = name
        self.stream = stream


class _TextPage:
    def __init__(self, text: str) -> None:
        self._text = text
        self.closed = False

    def get_text_range(self) -> str:
        return self._text

    def close(self) -> None:
        self.closed = True


class _PdfPage:
    def __init__(self, text: str) -> None:
        self._textpage = _TextPage(text)
        self.closed = False

    def get_textpage(self) -> _TextPage:
        return self._textpage

    def close(self) -> None:
        self.closed = True


class _PdfDocument:
    def __init__(self, state: _State) -> None:
        self._pages = [_PdfPage(text) for text in state.pdf_pages]
        self.closed = False

    def __len__(self) -> int:
        return len(self._pages)

    def __getitem__(self, index: int) -> _PdfPage:
        return self._pages[index]

    def close(self) -> None:
        self.closed = True


class _State:
    """Scripted stub state for one test."""

    def __init__(self) -> None:
        self.pdf_pages: list[str] = []
        self.items: list[object] = []
        self.convert_calls: list[tuple[str | None, tuple[int, int] | None]] = []
        self.convert_error: Exception | None = None
        self.delay_s = 0.0


def _grid_from_markdown(markdown: str) -> list[list[types.SimpleNamespace]]:
    """Cells of a Markdown table (separator row dropped) as a grid."""
    rows: list[list[types.SimpleNamespace]] = []
    for line in markdown.splitlines():
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and all(cell == "---" for cell in cells):
            continue
        rows.append([types.SimpleNamespace(text=cell) for cell in cells])
    return rows


# ── Light factory / descriptor (no heavy import) ────────────────────────────────


def test_descriptor_declares_only_verified_formats_and_stays_cpu_only() -> None:
    descriptor = docling_module.DESCRIPTOR
    assert descriptor.name == "docling"
    assert descriptor.version == docling_module.DOCLING_BACKEND_VERSION
    assert descriptor is docling_module.factory.descriptor
    capabilities = descriptor.capabilities
    assert capabilities.supported_formats == [
        "application/pdf",
        "text/html",
        "text/markdown",
        "text/plain",
    ]
    assert capabilities.optional_dependency_group == "docling"
    assert capabilities.supports_page_ranges is True
    assert capabilities.supports_multi_page is True
    assert capabilities.requires_gpu is False
    assert capabilities.estimated_vram_gb is None
    assert capabilities.model_asset is None


def test_factory_reports_the_missing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(_name: str) -> object:
        raise ImportError("docling")

    monkeypatch.setattr(importlib, "import_module", _raise)
    with pytest.raises(
        DependencyUnavailableError,
        match=re.escape("backend dependency 'docling' is not installed — install the 'docling' extra"),
    ) as excinfo:
        docling_module.factory(BackendConfig(name="docling"))
    assert (excinfo.value.module, excinfo.value.extra) == ("docling", "docling")
    assert isinstance(excinfo.value, BackendError)


def test_impl_without_create_entry_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib, "import_module", lambda _name: object())
    with pytest.raises(BackendError, match="must expose create"):
        docling_module.factory(BackendConfig(name="docling"))


# ── Heavy impl under the stub ───────────────────────────────────────────────────


def test_factory_returns_the_impl_backend(docling_stub: _State) -> None:
    backend = _backend()
    assert backend.name == "docling"
    assert backend.capabilities is docling_module.DESCRIPTOR.capabilities


def test_analyze_pdf_maps_page_signals(docling_stub: _State) -> None:
    docling_stub.pdf_pages = ["first page text", "   "]
    analysis = _backend().analyze(_PDF_SOURCE)
    assert analysis.page_count == 2
    assert analysis.source_hash == hashlib.sha256(_PDF_BYTES).hexdigest()
    first, second = analysis.signals
    assert (first.page_number, first.has_native_text, first.text_chars, first.blank) == (1, True, 15, False)
    assert (second.page_number, second.has_native_text, second.text_chars, second.blank) == (2, False, 3, True)


def test_analyze_pdf_without_pages_falls_back_to_one_blank_signal(docling_stub: _State) -> None:
    analysis = _backend().analyze(_PDF_SOURCE)
    assert analysis.page_count == 1
    signal = analysis.signals[0]
    assert (signal.page_number, signal.has_native_text, signal.text_chars, signal.blank) == (1, False, 0, True)


def test_analyze_reads_text_formats_directly(docling_stub: _State) -> None:
    source = SourceDocument(uri="file:///doc.md", media_type="text/markdown", content=b"# heading\n\nbody")
    analysis = _backend().analyze(source)
    assert analysis.page_count == 1
    assert analysis.signals[0].text_chars == len("# heading\n\nbody")
    blank = SourceDocument(uri="file:///empty.txt", media_type="text/plain", content=b"   ")
    assert _backend().analyze(blank).signals[0].blank is True


def test_converter_is_created_once_per_process(docling_stub: _State) -> None:
    impl = _impl()
    first = impl._converter()
    assert impl._converter() is first


def test_convert_builds_typed_ir_per_page(docling_stub: _State) -> None:
    table = _TableItem("| a | b |", page_no=1)
    docling_stub.items = [
        _TextItem("Title text", label="section_header", page_no=1),
        table,
        _TextItem("second page", page_no=2),
        _OtherItem(page_no=2),
    ]
    result = _convert(_backend(), page_range=PageRange(start=1, end=2))
    assert result.failures == []
    assert [page.page_number for page in result.pages] == [1, 2]
    assert [block.kind for block in result.pages[0].blocks] == [ChunkKind.HEADING, ChunkKind.TABLE]
    assert result.pages[0].blocks[0].id == "docling-1-b0"
    assert result.pages[0].blocks[1].content == "| a | b |"
    # structured rows: same cells as the Markdown content (round-trip rule)
    assert result.pages[0].blocks[1].rows == (("a", "b"),)
    content_cells = [cell.strip() for cell in result.pages[0].blocks[1].content.strip().strip("|").split("|")]
    assert [cell for row in result.pages[0].blocks[1].rows for cell in row] == content_cells
    assert table.last_doc is not None  # table export receives the document
    assert result.pages[1].blocks[0].content == "second page"
    assert result.backend.name == "docling"
    assert result.backend.version == docling_module.DOCLING_BACKEND_VERSION


def test_convert_text_format_omits_page_range_in_the_converter_call(docling_stub: _State) -> None:
    docling_stub.items = [_TextItem("body")]
    source = SourceDocument(uri="file:///doc.html", media_type="text/html", content=b"<p>body</p>")
    result = _convert(_backend(), source, page_range=PageRange(start=1, end=1))
    assert [page.page_number for page in result.pages] == [1]
    assert docling_stub.convert_calls[0][1] is None


def test_convert_defaults_to_page_one_when_no_chunks(docling_stub: _State) -> None:
    result = _convert(_backend())
    assert [page.page_number for page in result.pages] == [1]
    assert result.pages[0].blocks == []


def test_convert_fills_every_requested_page(docling_stub: _State) -> None:
    docling_stub.items = [_TextItem("only page 2", page_no=2)]
    result = _convert(_backend(), page_range=PageRange(start=1, end=3))
    assert [page.page_number for page in result.pages] == [1, 2, 3]
    assert result.pages[0].blocks == []
    assert result.pages[1].blocks[0].content == "only page 2"


def test_convert_filters_pages_outside_the_range(docling_stub: _State) -> None:
    docling_stub.items = [_TextItem("p1", page_no=1), _TextItem("p2", page_no=2), _TextItem("p3", page_no=3)]
    result = _convert(_backend(), page_range=PageRange(start=2, end=2))
    assert [page.page_number for page in result.pages] == [2]
    assert [block.content for block in result.pages[0].blocks] == ["p2"]


def test_convert_uses_page_one_for_items_without_provenance(docling_stub: _State) -> None:
    docling_stub.items = [_TextItem("no prov", page_no=None)]
    result = _convert(_backend())
    assert result.pages[0].page_number == 1


def test_convert_pre_cancel_is_typed(docling_stub: _State) -> None:
    result = _convert(_backend(), cancellation=lambda: True)
    assert result.pages == []
    failure = result.failures[0]
    assert failure.code is FailureCode.CANCELLED
    assert failure.pass_kind is PassKind.NATIVE
    assert failure.page_range is None


def test_convert_cancels_between_items(docling_stub: _State) -> None:
    docling_stub.items = [_TextItem(f"page {n}", page_no=n) for n in (1, 2, 3)]
    calls = {"n": 0}

    def _cancel_after_first() -> bool:
        calls["n"] += 1
        return calls["n"] >= 2  # pre-check passes, first item check stops

    result = _convert(_backend(), cancellation=_cancel_after_first)
    assert result.failures[0].code is FailureCode.CANCELLED


def test_convert_times_out_after_conversion(docling_stub: _State) -> None:
    docling_stub.items = [_TextItem("body")]
    docling_stub.delay_s = 0.05
    result = _convert(_backend(), timeout_s=0.01)
    assert result.pages == []
    assert result.failures[0].code is FailureCode.TIMEOUT


def test_convert_enforces_the_output_budget(docling_stub: _State) -> None:
    docling_stub.items = [_TextItem("0123456789"), _TextItem("xxxxxxxxxx")]
    result = _convert(_backend(), max_output_chars=10)
    assert [page.page_number for page in result.pages] == [1]
    assert result.failures[0].code is FailureCode.BUDGET_EXCEEDED


def test_convert_reports_conversion_errors_as_typed_failures(docling_stub: _State) -> None:
    docling_stub.convert_error = RuntimeError("pdfium exploded")
    result = _convert(_backend())
    assert result.pages == []
    failure = result.failures[0]
    assert failure.code is FailureCode.BACKEND_ERROR
    assert "RuntimeError: pdfium exploded" in failure.detail


def _backend() -> DocumentBackend:
    return docling_module.factory(BackendConfig(name="docling"))


def _convert(
    backend: DocumentBackend,
    source: SourceDocument = _PDF_SOURCE,
    *,
    page_range: PageRange | None = None,
    timeout_s: float | None = None,
    max_output_chars: int | None = None,
    cancellation: object = None,
) -> BackendResult:
    return backend.convert(
        ConversionRequest(
            source=source,
            page_range=page_range,
            timeout_s=timeout_s,
            max_output_chars=max_output_chars,
            cancellation=cancellation,  # type: ignore[arg-type]
        )
    )


def test_source_bytes_reads_local_files_and_wraps_errors(tmp_path: Path, docling_stub: _State) -> None:
    impl = _impl()
    target = tmp_path / "doc.txt"
    target.write_text("hello", encoding="utf-8")
    assert impl.source_bytes(SourceDocument(uri=target.absolute().as_uri())) == b"hello"
    with pytest.raises(BackendError, match="cannot read source"):
        impl.source_bytes(SourceDocument(uri=(tmp_path / "missing.txt").absolute().as_uri()))


@pytest.fixture
def docling_stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[_State]:
    """Install the stub package tree and force a fresh heavy-impl import."""
    state = _State()
    _install_stub_modules(state, monkeypatch)
    monkeypatch.delitem(sys.modules, "parsecraft.backends.docling._impl", raising=False)
    try:
        yield state
    finally:
        sys.modules.pop("parsecraft.backends.docling._impl", None)


def _install_stub_modules(state: _State, monkeypatch: pytest.MonkeyPatch) -> None:
    """Register the stub module tree the heavy impl imports from."""

    def module(name: str) -> types.ModuleType:
        mod = types.ModuleType(name)
        monkeypatch.setitem(sys.modules, name, mod)
        return mod

    pypdfium2 = module("pypdfium2")
    pypdfium2.PdfDocument = lambda _data: _PdfDocument(state)  # ty: ignore[unresolved-attribute]

    docling = module("docling")
    datamodel = module("docling.datamodel")
    base_models = module("docling.datamodel.base_models")
    document_mod = module("docling.datamodel.document")
    converter_mod = module("docling.document_converter")
    docling.datamodel = datamodel  # ty: ignore[unresolved-attribute]
    datamodel.document = document_mod  # ty: ignore[unresolved-attribute]
    document_mod.ConversionResult = _ConversionResult  # ty: ignore[unresolved-attribute]
    converter_mod.DocumentConverter = lambda: _Converter(state)  # ty: ignore[unresolved-attribute]
    base_models.DocumentStream = _DocumentStream  # ty: ignore[unresolved-attribute]

    docling_core = module("docling_core")
    types_mod = module("docling_core.types")
    doc_mod = module("docling_core.types.doc")
    doc_document = module("docling_core.types.doc.document")
    doc_io = module("docling_core.types.io")
    docling_core.types = types_mod  # ty: ignore[unresolved-attribute]
    types_mod.doc = doc_mod  # ty: ignore[unresolved-attribute]
    doc_mod.document = doc_document  # ty: ignore[unresolved-attribute]
    doc_document.DoclingDocument = _Document  # ty: ignore[unresolved-attribute]
    doc_document.TextItem = _TextItem  # ty: ignore[unresolved-attribute]
    doc_document.TableItem = _TableItem  # ty: ignore[unresolved-attribute]
    doc_io.DocumentStream = _DocumentStream  # ty: ignore[unresolved-attribute]


def _impl() -> types.ModuleType:
    return importlib.import_module("parsecraft.backends.docling._impl")
