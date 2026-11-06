"""Docling backend: light/heavy boundary, verified formats, bound-checked convert.

Fully offline: the heavy ``docling_rs`` / ``docling_core`` / ``pypdfium2`` imports
are satisfied by stub modules in ``sys.modules`` before ``_impl`` loads — the
same pattern as the LiteParse and OCR families.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib
import io
import os
import re
import sys
import tempfile
import time
import types
from collections.abc import Callable, Generator, Iterator
from pathlib import Path

import pytest

from parsecraft.backends.docling import docling as docling_module
from parsecraft.backends.errors import BackendError, DependencyUnavailableError
from parsecraft.backends.protocol import BackendConfig, BackendResult, ConversionRequest, DocumentBackend, SourceDocument
from parsecraft.ir.models import AssetKind, BBox, ChunkKind, FailureCode, PageRange, PassKind
from tests.fixtures.documents import docx_headings_bytes

_PDF_BYTES = b"%PDF-1.7 stub"
_PDF_SOURCE = SourceDocument(uri="file:///doc.pdf", media_type="application/pdf", content=_PDF_BYTES)


# Verbatim docling-rs 2.55.4 warning for a partial model cache (layout models
# present, OCR models absent) — captured from the real engine, fd 2, pc-04u.
# Built from text and encoded: a bytes literal cannot hold the em dash, and the
# bytes must stay verbatim or the fragment check would silently stop matching.
_OCR_WARNING = (
    "warning: OCR model unavailable (ocr: load .models/ocr_rec_en.onnx: File at "
    "`.models/ocr_rec_en.onnx` does not exist); continuing without OCR \u2014 scanned "
    "pages and text inside images will come back empty (run scripts/install/"
    "download_dependencies.sh for the model)\n"
).encode()


# ── Figure surface (pc-sc9): cropped pictures become assets + figure chunks ─────

_PNG_BYTES = b"\x89PNG-fixture-bytes"
_PNG_DATA_URI = "data:image/png;base64," + base64.b64encode(_PNG_BYTES).decode("ascii")


# ── Stubs: heavy packages faked through sys.modules ─────────────────────────────


class _Prov:
    def __init__(self, page_no: int, bbox: object | None = None) -> None:
        self.page_no = page_no
        self.bbox = bbox


class _CoordOrigin:
    """The real docling-core enum's surface: ``.name`` carries the frame."""

    def __init__(self, name: str) -> None:
        self.name = name


class _BoundingBox:
    """Mirrors docling-core ``BoundingBox``: l/t/r/b + ``coord_origin``."""

    def __init__(
        self,
        left: float,
        top: float,
        right: float,
        bottom: float,
        coord_origin: str = "BOTTOMLEFT",
    ) -> None:
        # Attribute names mirror the real docling-core BoundingBox (l/t/r/b),
        # which the adapter reads via getattr; only the params get safe names.
        self.l, self.t, self.r, self.b = left, top, right, bottom
        self.coord_origin = _CoordOrigin(coord_origin)


class _ImageRef:
    """Mirrors docling-core ``ImageRef``: data-URI crop + size + mimetype."""

    def __init__(self, uri: str, mimetype: str = "image/png", width: float = 247.0, height: float = 112.0) -> None:
        self.uri = uri
        self.mimetype = mimetype
        self.size = types.SimpleNamespace(width=width, height=height)
        self.dpi = 144.0


class _PictureItem:
    """Mirrors docling-core ``PictureItem``: crop, provenance, captions."""

    def __init__(
        self,
        page_no: int = 1,
        image: object | None = None,  # docling-core ImageRef shape (uri, mimetype, size)
        bbox: object | None = None,  # docling-core BoundingBox shape (l/t/r/b, coord_origin)
        caption: str = "",
    ) -> None:
        self.label = "picture"
        self.prov = [_Prov(page_no, bbox)]
        self.image = image
        self.captions: list[object] = []
        self._caption = caption

    def caption_text(self, _doc: object) -> str:
        return self._caption


class _TextItem:
    def __init__(self, text: str, label: str = "text", page_no: int | None = 1, level: int | None = None) -> None:
        self.text = text
        self.label = label
        self.level = level
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
    def __init__(self, state: _State, options: dict[str, str | int | float | bool] | None = None) -> None:
        self._state = state
        self.options = dict(options or {})

    def convert(self, source: object, page_range: tuple[int, int] | None = None) -> _ConversionResult:
        self._state.convert_calls.append((getattr(source, "name", None), page_range))
        for blob in self._state.stderr_warnings:
            # Real fd-2 writes, like the Rust engine: exercises the adapter's
            # dup2 capture path, not a Python-level stderr stub (pc-04u).
            os.write(2, blob)
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
    def __init__(self, text: str, size: tuple[float, float] | None = (612.0, 792.0)) -> None:
        self._textpage = _TextPage(text)
        self._size = size
        self.closed = False

    def get_textpage(self) -> _TextPage:
        return self._textpage

    def get_size(self) -> tuple[float, float]:
        """Point dimensions, like the real pypdfium2 page (pc-sc9 y-flip)."""
        if self._size is None:
            raise RuntimeError("size probe failed")
        return self._size

    def close(self) -> None:
        self.closed = True


class _PdfDocument:
    def __init__(self, state: _State) -> None:
        sizes = state.pdf_sizes or [(612.0, 792.0)] * max(len(state.pdf_pages), 1)
        self._pages = [_PdfPage(text, size) for text, size in zip(state.pdf_pages, sizes, strict=False)]
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
        self.pdf_sizes: list[tuple[float, float] | None] = []
        self.pdf_open_error: Exception | None = None
        self.items: list[object] = []
        self.convert_calls: list[tuple[str | None, tuple[int, int] | None]] = []
        self.convert_error: Exception | None = None
        self.delay_s = 0.0
        self.stderr_warnings: list[bytes] = []
        self.converters: list[_Converter] = []


class _MutePicture(_PictureItem):
    """A picture whose engine object lacks the caption accessor."""

    caption_text = None  # type: ignore[assignment]


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
    # Markdown/plain-text withdrawn 2026-10-30 (marko table-matcher CPU spin):
    # they route to native-markdown/native-text instead.
    # DOC (legacy Word 97-2003 binary) added 2026-11-06, conversion-verified
    # against the real engine — the same bar DOCX met.
    assert capabilities.supported_formats == [
        "application/pdf",
        "text/html",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]
    assert capabilities.optional_dependency_group == "docling"
    assert capabilities.supports_page_ranges is True
    assert capabilities.supports_multi_page is True
    assert capabilities.gpu_requirement == 0.0
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


def test_convert_declines_markdown_with_the_native_redirect(docling_stub: _State) -> None:
    source = SourceDocument(uri="file:///doc.md", media_type="text/markdown", content=b"# heading\n\nbody")
    result = _convert(_backend(), source)
    assert result.pages == []
    assert docling_stub.convert_calls == []  # heavy converter never touched
    failure = result.failures[0]
    assert failure.code is FailureCode.BACKEND_ERROR
    assert "text/markdown" in failure.detail
    assert "native-markdown" in failure.detail


def test_convert_declines_plain_text_with_the_native_redirect(docling_stub: _State) -> None:
    source = SourceDocument(uri="file:///doc.txt", media_type="text/plain", content=b"body")
    result = _convert(_backend(), source)
    assert result.pages == []
    assert docling_stub.convert_calls == []
    failure = result.failures[0]
    assert failure.code is FailureCode.BACKEND_ERROR
    assert "text/plain" in failure.detail
    assert "native-text" in failure.detail


def test_converter_is_created_once_per_option_set(docling_stub: _State) -> None:
    impl = _impl()
    first = impl._converter({})
    assert impl._converter({}) is first


def test_converter_is_rebuilt_for_a_different_option_set(docling_stub: _State) -> None:
    """Two option sets must never share an engine (pc-fwn)."""
    impl = _impl()
    plain = impl._converter({})
    quiet = impl._converter({"do_ocr": False})
    assert quiet is not plain
    assert plain.options == {}
    assert quiet.options == {"do_ocr": False}
    assert impl._converter({"do_ocr": False}) is quiet


def test_backend_honours_do_ocr_from_the_config(docling_stub: _State) -> None:
    """``--backend-option do_ocr=false`` reaches the engine (pc-fwn)."""
    backend = docling_module.factory(BackendConfig(name="docling", options={"offline": False, "do_ocr": "false"}))
    _convert(backend)
    assert docling_stub.converters[-1].options == {"do_ocr": False}


def test_backend_without_options_uses_the_bare_converter(docling_stub: _State) -> None:
    _convert(_backend())
    assert docling_stub.converters[-1].options == {}


def test_unknown_option_is_rejected_naming_it(docling_stub: _State) -> None:
    """A silently ignored option looks like success, so it must raise (pc-fwn)."""
    with pytest.raises(BackendError, match="unknown docling option 'do_ocrr'"):
        docling_module.factory(BackendConfig(name="docling", options={"do_ocrr": False}))


def test_bad_option_type_is_rejected_naming_the_option(docling_stub: _State) -> None:
    with pytest.raises(BackendError, match="'do_ocr' must be a boolean"):
        docling_module.factory(BackendConfig(name="docling", options={"do_ocr": "maybe"}))
    with pytest.raises(BackendError, match="'images_scale' must be a number"):
        docling_module.factory(BackendConfig(name="docling", options={"images_scale": "big"}))
    with pytest.raises(BackendError, match="'ocr_lang' must be text"):
        docling_module.factory(BackendConfig(name="docling", options={"ocr_lang": True}))


def test_injected_offline_option_is_dropped_not_forwarded(docling_stub: _State) -> None:
    """The executor puts ``offline`` in every map; it is not a constructor keyword."""
    assert _impl().converter_options({"offline": True}) == {}


def test_numeric_option_is_coerced_to_a_float(docling_stub: _State) -> None:
    assert _impl().converter_options({"images_scale": "2"}) == {"images_scale": 2.0}


def test_text_option_passes_through(docling_stub: _State) -> None:
    assert _impl().converter_options({"ocr_lang": "deu"}) == {"ocr_lang": "deu"}


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


@pytest.mark.parametrize(
    ("label", "level", "expected"),
    [
        ("title", None, "1"),
        ("section_header", 1, "1"),
        ("section_header", 2, "2"),
        ("section_header", 3, "3"),
        ("section_header", 9, "6"),  # clamped at the source: ir.markdown honours 1-6 only
    ],
)
def test_convert_marks_heading_levels_with_the_author_level(docling_stub: _State, label: str, level: int | None, expected: str) -> None:
    docling_stub.items = [_TextItem("Heading text", label=label, level=level)]
    result = _convert(_backend())
    assert result.pages[0].blocks[0].metadata == {"heading_level": expected}


def test_convert_omits_heading_level_without_a_docling_level(docling_stub: _State) -> None:
    docling_stub.items = [_TextItem("Unlevelled header", label="section_header")]
    result = _convert(_backend())
    assert "heading_level" not in result.pages[0].blocks[0].metadata


def test_convert_omits_heading_level_on_body_text(docling_stub: _State) -> None:
    docling_stub.items = [_TextItem("Body under the first top-level heading.")]
    result = _convert(_backend())
    assert "heading_level" not in result.pages[0].blocks[0].metadata


def test_docx_headings_fixture_carries_title_deep_headings_and_body() -> None:
    document = importlib.import_module("docx").Document(io.BytesIO(docx_headings_bytes()))
    assert document.paragraphs
    styles = [paragraph.style.name if paragraph.style is not None else None for paragraph in document.paragraphs]
    assert styles == ["Title", "Heading 1", "Normal", "Heading 2", "Heading 3", "Heading 9"]


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


def test_convert_missing_models_names_the_download_helper(docling_stub: _State) -> None:
    docling_stub.convert_error = RuntimeError(
        "parse error: pdf: layout: model not found at .models/layout_heron.onnx — PDF/image conversion needs the ONNX models"
    )
    result = _convert(_backend())
    assert result.pages == []
    failure = result.failures[0]
    assert failure.code is FailureCode.BACKEND_ERROR
    assert "docling_rs.download_models()" in failure.detail
    assert "never downloaded implicitly" in failure.detail


def test_convert_warns_on_pages_emptied_by_missing_ocr_models(docling_stub: _State) -> None:
    """Partial cache: engine warns on fd 2, empty pages get the typed warning (pc-04u)."""
    docling_stub.stderr_warnings = [_OCR_WARNING]
    docling_stub.items = [_TextItem("kept text", page_no=1)]
    result = _convert(_backend(), page_range=PageRange(start=1, end=2))
    assert [f.code for f in result.failures] == []
    kept, emptied = result.pages
    assert [c.content for c in kept.blocks] == ["kept text"]
    assert kept.diagnostics == []  # the page with content is unaffected
    assert emptied.blocks == []
    (diagnostic,) = emptied.diagnostics
    assert diagnostic.level.value == "warning"
    assert diagnostic.code == "docling-ocr-unavailable"
    assert "docling_rs.download_models()" in diagnostic.message
    assert "came back empty for this page" in diagnostic.message


def test_convert_replays_the_engine_warning_to_stderr(docling_stub: _State) -> None:
    """The capture observes the warning, it never swallows it (pc-04u)."""
    docling_stub.stderr_warnings = [_OCR_WARNING]
    window, read = _fd2_capture()
    with window:
        result = _convert(_backend())
    assert result.pages[0].diagnostics[0].code == "docling-ocr-unavailable"
    assert _OCR_WARNING in read()  # replayed to (this window's) fd 2 verbatim


def test_convert_replays_the_engine_warning_when_the_pass_raises(docling_stub: _State) -> None:
    """A raising pass still replays what the engine printed before it threw.

    The replay must happen in the capture's ``finally``: putting it after the
    block silently dropped these bytes, because the exception left the window
    without replaying anything (pc-04u, measured against the branch).
    """
    docling_stub.stderr_warnings = [_OCR_WARNING]
    docling_stub.convert_error = RuntimeError("model not found at .models/layout_heron.onnx")
    window, read = _fd2_capture()
    with window:
        result = _convert(_backend())
    assert [f.code for f in result.failures] == [FailureCode.BACKEND_ERROR]
    assert _OCR_WARNING in read()


def _fd2_capture() -> tuple[contextlib.AbstractContextManager[None], Callable[[], bytes]]:
    """Test-side fd-2 capture: ``(window, read)``.

    Opening ``window`` redirects fd 2 into a temp file (nesting inside the
    adapter's own capture is fine: the adapter replays to *its* restored fd 2,
    which is this window's file); ``read()`` returns everything captured once
    the window closed (pc-04u).
    """
    holder: dict[str, bytes] = {}
    fd, path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)

    @contextlib.contextmanager
    def _window() -> Generator[None]:
        backup = os.dup(2)
        fd = os.open(path, os.O_RDWR | os.O_TRUNC)
        os.dup2(fd, 2)
        try:
            yield
        finally:
            sys.stderr.flush()
            os.dup2(backup, 2)
            os.close(backup)
            os.lseek(fd, 0, os.SEEK_SET)
            holder["blob"] = os.read(fd, 1 << 20)
            os.close(fd)

    def _read() -> bytes:
        return holder["blob"]

    return _window(), _read


def test_convert_ignores_unrelated_engine_warnings(docling_stub: _State) -> None:
    """Only the OCR-model headline matches; other Rust warnings stay quiet."""
    docling_stub.stderr_warnings = [b"warning: table structure disabled by config\n", b"OCR model"]
    result = _convert(_backend())
    assert result.pages[0].blocks == []
    assert result.pages[0].diagnostics == []


def test_convert_without_ocr_warning_keeps_pages_clean(docling_stub: _State) -> None:
    """No warning, no diagnostics — the healthy path is unchanged (pc-04u)."""
    docling_stub.items = [_TextItem("hello", page_no=1)]
    result = _convert(_backend())
    assert [c.content for c in result.pages[0].blocks] == ["hello"]
    assert result.pages[0].diagnostics == []


def test_pages_emptied_by_do_ocr_false_say_so(docling_stub: _State) -> None:
    """The caller's own knob must not produce a silent empty page (pc-fwn).

    docling.rs prints no warning at all when OCR is off, so the adapter has to
    supply the diagnosis; the executor's ``degraded-empty-output`` skips any
    page that already carries diagnostics, so the specific fact wins and the
    two never double-speak.
    """
    docling_stub.items = [_TextItem("kept text", page_no=1)]
    backend = docling_module.factory(BackendConfig(name="docling", options={"do_ocr": False}))
    result = _convert(backend, page_range=PageRange(start=1, end=2))
    kept, emptied = result.pages
    assert [c.content for c in kept.blocks] == ["kept text"]
    assert kept.diagnostics == []
    assert emptied.blocks == []
    (diagnostic,) = emptied.diagnostics
    assert diagnostic.level.value == "warning"
    assert diagnostic.code == "docling-ocr-disabled"
    assert "do_ocr=false" in diagnostic.message
    assert "came back empty" in diagnostic.message


def test_do_ocr_false_reports_no_missing_models_and_no_pages_are_blank(docling_stub: _State) -> None:
    """Nothing to say when the option is off but every page produced text."""
    docling_stub.items = [_TextItem("hello", page_no=1)]
    backend = docling_module.factory(BackendConfig(name="docling", options={"do_ocr": False}))
    result = _convert(backend)
    assert [c.content for c in result.pages[0].blocks] == ["hello"]
    assert result.pages[0].diagnostics == []


def test_convert_emits_figure_assets_and_chunks_like_mineru(docling_stub: _State) -> None:
    """One cropped picture yields one asset + one FIGURE chunk linking it.

    pc-sc9 acceptance: same surface as mineru — ExtractedAsset
    (kind, media type, data URI, bbox, sha256) plus a figure chunk whose
    metadata carries ``asset_id``; page number and bbox come from the
    item's provenance, flipped out of docling's y-up frame.
    """
    docling_stub.pdf_pages = [""]  # one blank PDF page: heights for the y-flip
    docling_stub.items = [
        _TextItem("before", page_no=1),
        _PictureItem(page_no=1, image=_ImageRef(_PNG_DATA_URI), bbox=_BoundingBox(left=390.87, top=133.03, right=513.98, bottom=77.34), caption="Logo"),
        _TextItem("after", page_no=1),
    ]
    result = _convert(_backend())
    (page,) = result.pages
    assert [c.kind for c in page.blocks] == [ChunkKind.PARAGRAPH, ChunkKind.FIGURE, ChunkKind.PARAGRAPH]
    figure = page.blocks[1]
    (asset,) = page.assets
    assert asset.id == "docling-1-a0"
    assert asset.kind == AssetKind.IMAGE
    assert asset.media_type == "image/png"
    assert asset.uri == _PNG_DATA_URI
    assert asset.sha256 == hashlib.sha256(_PNG_BYTES).hexdigest()
    assert asset.bbox == BBox(x0=390.87, y0=658.97, x1=513.98, y1=714.66)
    assert figure.id == "docling-1-b1"
    assert figure.metadata == {"backend": "docling", "asset_id": "docling-1-a0"}
    assert figure.content == "Logo"
    assert figure.reading_order == 1


def test_figure_bbox_flips_the_bottomleft_provenance_frame(docling_stub: _State) -> None:
    """Docling provenance is y-up (t > b); the IR frame is raster y-down."""
    docling_stub.pdf_pages = [""]  # one blank PDF page: heights for the y-flip
    docling_stub.items = [_PictureItem(page_no=1, image=_ImageRef(_PNG_DATA_URI), bbox=_BoundingBox(left=10.0, top=133.03, right=60.0, bottom=77.34))]
    (asset,) = _convert(_backend()).pages[0].assets
    assert asset.bbox == BBox(x0=10.0, y0=658.97, x1=60.0, y1=714.66)


def test_topleft_provenance_is_taken_as_is(docling_stub: _State) -> None:
    docling_stub.items = [
        _PictureItem(page_no=1, image=_ImageRef(_PNG_DATA_URI), bbox=_BoundingBox(left=10.0, top=20.0, right=60.0, bottom=80.0, coord_origin="TOPLEFT"))
    ]
    (asset,) = _convert(_backend()).pages[0].assets
    assert asset.bbox == BBox(x0=10.0, y0=20.0, x1=60.0, y1=80.0)


def test_provenance_without_a_bbox_yields_an_asset_without_one(docling_stub: _State) -> None:
    docling_stub.items = [_PictureItem(page_no=1, image=_ImageRef(_PNG_DATA_URI), bbox=None)]
    (asset,) = _convert(_backend()).pages[0].assets
    assert asset.bbox is None


def test_malformed_provenance_bbox_vanishes_but_the_asset_stays(docling_stub: _State) -> None:
    """Non-numeric corners drop the box, not the asset (mineru parity)."""
    docling_stub.pdf_pages = [""]  # PDF media: the BOTTOMLEFT branch needs a page height to reach float()
    broken = types.SimpleNamespace(l="not-a-number", t=1.0, r=2.0, b=3.0, coord_origin=_CoordOrigin("BOTTOMLEFT"))
    docling_stub.items = [_PictureItem(page_no=1, image=_ImageRef(_PNG_DATA_URI), bbox=broken)]
    (asset,) = _convert(_backend()).pages[0].assets
    assert asset.bbox is None


def test_uncropped_picture_is_skipped_entirely(docling_stub: _State) -> None:
    """``keep_picture_images=false`` drops the crop: no asset, no dangling chunk."""
    docling_stub.items = [_PictureItem(page_no=1, image=None)]
    (page,) = _convert(_backend()).pages
    assert page.blocks == []
    assert page.assets == []


def test_uncropped_picture_on_an_ocr_disabled_page_still_says_so(docling_stub: _State) -> None:
    """Composition (pc-fwn): the figure machinery must not mute empty-page warnings.

    The picture produced nothing (no crop), so the page has no blocks and
    the pc-fwn ``docling-ocr-disabled`` diagnostic still fires — assets are
    not blocks and never silence that path.
    """
    docling_stub.items = [_PictureItem(page_no=1, image=None)]
    backend = docling_module.factory(BackendConfig(name="docling", options={"do_ocr": False}))
    (page,) = _convert(backend).pages
    assert page.assets == []
    (diagnostic,) = page.diagnostics
    assert diagnostic.code == "docling-ocr-disabled"


def test_captioned_figure_page_is_not_reported_empty(docling_stub: _State) -> None:
    """Pinned decision: figure chunks are blocks, so a figure-bearing page did
    not "come back empty" and the pc-fwn/pc-04u diagnostics stay quiet.
    """
    docling_stub.items = [_PictureItem(page_no=1, image=_ImageRef(_PNG_DATA_URI), caption="Figure 1")]
    backend = docling_module.factory(BackendConfig(name="docling", options={"do_ocr": False}))
    (page,) = _convert(backend).pages
    assert [c.kind for c in page.blocks] == [ChunkKind.FIGURE]
    assert page.assets
    assert page.diagnostics == []


def test_figure_on_a_page_outside_the_range_is_dropped(docling_stub: _State) -> None:
    docling_stub.items = [_PictureItem(page_no=2, image=_ImageRef(_PNG_DATA_URI))]
    result = _convert(_backend(), page_range=PageRange(start=1, end=1))
    assert result.pages[0].blocks == []
    assert result.pages[0].assets == []


def test_ranges_past_the_document_are_padded_without_crashing(docling_stub: _State) -> None:
    docling_stub.items = [_PictureItem(page_no=2, image=_ImageRef(_PNG_DATA_URI))]
    result = _convert(_backend(), page_range=PageRange(start=1, end=3))
    assert [p.page_number for p in result.pages] == [1, 2, 3]
    assert result.pages[2].blocks == []
    assert result.pages[2].assets == []
    assert [a.id for a in result.pages[1].assets] == ["docling-2-a0"]


def test_document_without_figures_has_no_assets(docling_stub: _State) -> None:
    docling_stub.items = [_TextItem("para", page_no=1), _TableItem("| a | b |\n| - | - |\n| 1 | 2 |", page_no=1)]
    result = _convert(_backend())
    assert all(page.assets == [] for page in result.pages)


def test_non_data_uri_crop_is_skipped(docling_stub: _State) -> None:
    docling_stub.items = [_PictureItem(page_no=1, image=_ImageRef("file:///elsewhere.png"))]
    (page,) = _convert(_backend()).pages
    assert page.blocks == []
    assert page.assets == []


def test_corrupt_base64_crop_is_skipped(docling_stub: _State) -> None:
    docling_stub.items = [_PictureItem(page_no=1, image=_ImageRef("data:image/png;base64,!!!not-base64!!!"))]
    (page,) = _convert(_backend()).pages
    assert page.blocks == []
    assert page.assets == []


def test_bare_engine_picture_still_yields_asset(docling_stub: _State) -> None:
    """A picture object without caption_text/mimetype niceties still emits."""
    docling_stub.items = [
        _MutePicture(page_no=1, image=types.SimpleNamespace(uri=_PNG_DATA_URI), bbox=_BoundingBox(left=0.0, top=0.0, right=10.0, bottom=10.0))
    ]
    (page,) = _convert(_backend()).pages
    (asset,) = page.assets
    assert asset.media_type == "image/png"
    assert page.blocks[0].content == ""


def test_two_figures_on_one_page_get_distinct_ids(docling_stub: _State) -> None:
    docling_stub.items = [
        _PictureItem(page_no=1, image=_ImageRef(_PNG_DATA_URI)),
        _PictureItem(page_no=1, image=_ImageRef(_PNG_DATA_URI)),
    ]
    (page,) = _convert(_backend()).pages
    assert [c.id for c in page.blocks] == ["docling-1-b0", "docling-1-b1"]
    assert [c.metadata["asset_id"] for c in page.blocks] == ["docling-1-a0", "docling-1-a1"]
    assert [c.reading_order for c in page.blocks] == [0, 1]


def test_figure_captions_count_toward_the_output_budget(docling_stub: _State) -> None:
    """Caption text is chunk content and counts; the crop payload never does."""
    docling_stub.items = [_PictureItem(page_no=1, image=_ImageRef(_PNG_DATA_URI), caption="x" * 50)]
    result = _convert(_backend(), max_output_chars=10)
    (failure,) = result.failures
    assert failure.code == FailureCode.BUDGET_EXCEEDED
    assert result.pages[0].blocks == []


def test_pdf_page_heights_degrade_when_pypdfium2_cannot_open(docling_stub: _State) -> None:
    """Damaged PDFs that docling repaired still emit assets — with bbox=None."""
    docling_stub.pdf_open_error = RuntimeError("damaged")
    docling_stub.items = [_PictureItem(page_no=1, image=_ImageRef(_PNG_DATA_URI), bbox=_BoundingBox(left=10.0, top=20.0, right=60.0, bottom=80.0))]
    (asset,) = _convert(_backend()).pages[0].assets
    assert asset.bbox is None


def test_pdf_page_heights_degrade_when_a_page_size_fails(docling_stub: _State) -> None:
    docling_stub.pdf_pages = [""]  # one real page, or the zip truncates to zero
    docling_stub.pdf_sizes = [None]
    docling_stub.items = [_PictureItem(page_no=1, image=_ImageRef(_PNG_DATA_URI), bbox=_BoundingBox(left=10.0, top=20.0, right=60.0, bottom=80.0))]
    (asset,) = _convert(_backend()).pages[0].assets
    assert asset.bbox is None


def test_partial_provenance_bbox_yields_an_asset_without_one(docling_stub: _State) -> None:
    """A bbox missing one coordinate attribute degrades to bbox=None (pc-sc9)."""
    partial = types.SimpleNamespace(l=10.0, t=20.0, b=80.0, coord_origin=_CoordOrigin("BOTTOMLEFT"))
    docling_stub.items = [_PictureItem(page_no=1, image=_ImageRef(_PNG_DATA_URI), bbox=partial)]
    (asset,) = _convert(_backend()).pages[0].assets
    assert asset.bbox is None


def test_non_pdf_sources_emit_assets_without_bboxes(docling_stub: _State) -> None:
    """No page-height frame exists outside PDFs: asset without bbox (policy)."""
    source = SourceDocument(uri="file:///doc.html", media_type="text/html", content=b"<p>x</p>")
    docling_stub.items = [_PictureItem(page_no=1, image=_ImageRef(_PNG_DATA_URI), bbox=_BoundingBox(left=10.0, top=20.0, right=60.0, bottom=80.0))]
    (asset,) = _convert(_backend(), source=source).pages[0].assets
    assert asset.bbox is None


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

    def _make_pdf_document(_data: object) -> _PdfDocument:
        if state.pdf_open_error is not None:
            raise state.pdf_open_error
        return _PdfDocument(state)

    pypdfium2.PdfDocument = _make_pdf_document  # ty: ignore[unresolved-attribute]

    docling_rs = module("docling_rs")
    docling_rs.ConversionResult = _ConversionResult  # ty: ignore[unresolved-attribute]

    def _make_converter(**options: str | int | float | bool) -> _Converter:
        converter = _Converter(state, options)
        state.converters.append(converter)
        return converter

    docling_rs.DocumentConverter = _make_converter  # ty: ignore[unresolved-attribute]
    docling_rs.DocumentStream = _DocumentStream  # ty: ignore[unresolved-attribute]

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
    doc_document.PictureItem = _PictureItem  # ty: ignore[unresolved-attribute]
    doc_io.DocumentStream = _DocumentStream  # ty: ignore[unresolved-attribute]


def _impl() -> types.ModuleType:
    return importlib.import_module("parsecraft.backends.docling._impl")
