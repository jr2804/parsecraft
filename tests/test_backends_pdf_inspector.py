"""pdf-inspector backend: light/heavy boundary, verified formats, bound-checked convert.

Fully offline: the heavy ``pdf_inspector`` extension is satisfied by a stub
module in ``sys.modules`` before ``_impl`` loads — the same pattern as the
Docling, LiteParse, and OCR families.
"""

from __future__ import annotations

import hashlib
import importlib
import re
import sys
import time
import types
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from parsecraft.backends.errors import BackendError, DependencyUnavailableError
from parsecraft.backends.pdf_inspector import pdf_inspector as backend_module
from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    BackendRef,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
    SourceDocument,
)
from parsecraft.backends.registry import BackendRegistry
from parsecraft.ir import to_markdown
from parsecraft.ir.models import ChunkKind, FailureCode, PageRange, PageResult, PassKind, StructuredChunk
from parsecraft.pipeline import execute
from parsecraft.routing import Intent, RoutingConstraints

_PDF_BYTES = b"%PDF-1.7 stub"
_PDF_SOURCE = SourceDocument(uri="file:///doc.pdf", media_type="application/pdf", content=_PDF_BYTES)
_PRODUCED = datetime(2026, 10, 1, tzinfo=UTC)


# ── Stubs: the heavy `pdf_inspector` extension, faked through sys.modules ───────


class _PageMarkdown:
    """Stand-in for pdf_inspector.PageMarkdown (fields the impl reads)."""

    def __init__(self, page: int, markdown: str, needs_ocr: bool = False, ocr_reason: str | None = None) -> None:
        self.page = page
        self.markdown = markdown
        self.needs_ocr = needs_ocr
        self.ocr_reason = ocr_reason


class _PagesExtractionResult:
    def __init__(self, pages: list[_PageMarkdown]) -> None:
        self.pages = pages
        self.pages_with_tables: list[int] = []
        self.pages_with_columns: list[int] = []
        self.pages_needing_ocr: list[int] = []
        self.ocr_reasons_by_page: list[object] = []
        self.is_complex = False


class _Stub:
    """Fake ``pdf_inspector`` package: state scripted by each test."""

    def __init__(self) -> None:
        self.pages: list[_PageMarkdown] = []
        self.extract_error: Exception | None = None
        self.extract_delay_s = 0.0
        self.extract_pages_args: list[list[int] | None] = []


class _FallbackBackend:
    """Minimal native-shaped candidate used as the second eligible backend."""

    name = "zzz-native"
    capabilities = BackendCapabilities(supported_formats=["application/pdf"])

    def convert(self, request: ConversionRequest) -> BackendResult:
        page_range = request.page_range
        numbers = list(range(page_range.start, page_range.end + 1)) if page_range is not None else [1]
        return BackendResult(
            backend=BackendRef(name=self.name, version="0.1.0"),
            pages=[
                PageResult(
                    page_number=number,
                    blocks=[
                        StructuredChunk(
                            id=f"zzz-native-{number}",
                            kind=ChunkKind.PARAGRAPH,
                            content=f"fallback content {number}",
                            page_number=number,
                            reading_order=0,
                        )
                    ],
                )
                for number in numbers
            ],
            elapsed_s=0.01,
        )

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        raise AssertionError("the pipeline dispatches convert() only")


class _FallbackFactory:
    descriptor = BackendDescriptor(
        name="zzz-native",
        version="0.1.0",
        capabilities=_FallbackBackend.capabilities,
    )

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        return _FallbackBackend()


# ── Light factory / descriptor (no heavy import) ────────────────────────────────


def test_descriptor_declares_only_verified_formats_and_stays_cpu_only() -> None:
    descriptor = backend_module.DESCRIPTOR
    assert descriptor.name == "pdf-inspector"
    assert descriptor.version == backend_module.PDF_INSPECTOR_BACKEND_VERSION
    assert descriptor is backend_module.factory.descriptor
    capabilities = descriptor.capabilities
    # Verified against pdf-inspector 1.25.2 — the library is PDF-only:
    assert capabilities.supported_formats == ["application/pdf"]
    assert capabilities.optional_dependency_group == "pdf-inspector"
    assert capabilities.supports_page_ranges is True
    assert capabilities.supports_multi_page is True
    assert capabilities.gpu_requirement == 0.0
    assert capabilities.estimated_vram_gb is None
    # No OCR runtime is ever routed, so no model asset is declared:
    assert capabilities.model_asset is None


def test_entry_point_module_is_registry_ready() -> None:
    registry = BackendRegistry()
    registry._entry_points_loaded = True  # isolate from installed entry points
    registry.register("pdf-inspector", backend_module.factory)
    assert registry.get("pdf-inspector").name == "pdf-inspector"
    assert [d.name for d in registry.list_backends()] == ["pdf-inspector"]


def test_factory_reports_the_missing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(_name: str) -> object:
        raise ImportError("pdf_inspector")

    monkeypatch.setattr(importlib, "import_module", _raise)
    with pytest.raises(
        DependencyUnavailableError,
        match=re.escape("backend dependency 'pdf_inspector' is not installed — install the 'pdf-inspector' extra"),
    ) as excinfo:
        backend_module.factory(BackendConfig(name="pdf-inspector"))
    assert (excinfo.value.module, excinfo.value.extra) == ("pdf_inspector", "pdf-inspector")
    assert isinstance(excinfo.value, BackendError)


def test_impl_without_create_entry_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib, "import_module", lambda _name: object())
    with pytest.raises(BackendError, match="must expose create"):
        backend_module.factory(BackendConfig(name="pdf-inspector"))


# ── Heavy impl under the stub ───────────────────────────────────────────────────


def test_factory_returns_the_impl_backend(pdf_inspector_stub: _Stub) -> None:
    backend = _backend()
    assert backend.name == "pdf-inspector"
    assert backend.capabilities is backend_module.DESCRIPTOR.capabilities


def test_analyze_maps_page_signals(pdf_inspector_stub: _Stub) -> None:
    pdf_inspector_stub.pages = [
        _PageMarkdown(0, "# Page one\n\nbody text\n"),
        _PageMarkdown(1, "   ", needs_ocr=True, ocr_reason="no-text"),
        _PageMarkdown(2, "bad \ufffd\ufffd data"),
        _PageMarkdown(3, "", needs_ocr=True),
    ]
    analysis = _backend().analyze(_PDF_SOURCE)
    assert analysis.page_count == 4
    assert analysis.source_hash == hashlib.sha256(_PDF_BYTES).hexdigest()
    first, second, third, empty = analysis.signals
    assert (first.page_number, first.has_native_text, first.text_chars, first.blank) == (1, True, 22, False)
    assert first.replacement_char_ratio == 0.0
    # pdf-inspector's own per-page OCR verdict wins over the text volume:
    assert (second.page_number, second.has_native_text, second.blank) == (2, False, True)
    assert second.replacement_char_ratio == 0.0  # whitespace is text; 0 replacement chars
    assert third.replacement_char_ratio == pytest.approx(2 / 11)
    assert (empty.text_chars, empty.blank, empty.replacement_char_ratio) == (0, True, None)  # no text → no ratio
    assert pdf_inspector_stub.extract_pages_args == [None]  # one whole-document pass


def test_analyze_wraps_inspection_failures_in_a_typed_error(pdf_inspector_stub: _Stub) -> None:
    pdf_inspector_stub.extract_error = ValueError("Not a PDF: file appears to be plain text")
    with pytest.raises(BackendError, match="pdf-inspector cannot inspect source"):
        _backend().analyze(_PDF_SOURCE)


def test_convert_builds_typed_ir_per_page(pdf_inspector_stub: _Stub) -> None:
    pdf_inspector_stub.pages = [_PageMarkdown(0, "## Page one\n"), _PageMarkdown(1, "### Page two\n\n- item\n")]
    request = _request(page_range=PageRange(start=1, end=2))
    result = _backend().convert(request)
    assert result.failures == []
    assert [page.page_number for page in result.pages] == [1, 2]
    first = result.pages[0].blocks[0]
    assert (first.id, first.kind, first.content, first.reading_order) == ("pdf-inspector-p1-b0", ChunkKind.PARAGRAPH, "## Page one", 0)
    assert first.metadata == {"backend": "pdf-inspector"}
    assert result.pages[1].blocks[0].id == "pdf-inspector-p2-b0"
    assert result.backend.name == "pdf-inspector"
    assert result.backend.version == backend_module.PDF_INSPECTOR_BACKEND_VERSION
    assert pdf_inspector_stub.extract_pages_args == [None]  # one whole-document pass, filtered in Python


def test_convert_fills_pages_without_content(pdf_inspector_stub: _Stub) -> None:
    pdf_inspector_stub.pages = [_PageMarkdown(0, "first", needs_ocr=False), _PageMarkdown(1, "   ", needs_ocr=True)]
    result = _backend().convert(_request())
    assert [page.page_number for page in result.pages] == [1, 2]
    assert result.pages[0].blocks[0].content == "first"
    assert result.pages[1].blocks == []


def test_convert_filters_pages_outside_the_range(pdf_inspector_stub: _Stub) -> None:
    pdf_inspector_stub.pages = [_PageMarkdown(n - 1, f"page {n}") for n in (1, 2, 3)]
    result = _backend().convert(_request(page_range=PageRange(start=2, end=3)))
    assert [page.page_number for page in result.pages] == [2, 3]
    assert [block.content for page in result.pages for block in page.blocks] == ["page 2", "page 3"]


def test_convert_clamps_a_range_end_past_the_document(pdf_inspector_stub: _Stub) -> None:
    pdf_inspector_stub.pages = [_PageMarkdown(n - 1, f"page {n}") for n in (1, 2)]
    result = _backend().convert(_request(page_range=PageRange(start=2, end=9)))
    assert [page.page_number for page in result.pages] == [2]


@pytest.mark.parametrize(
    ("pages", "page_range", "detail_fragment"),
    [
        ([], None, "no pages"),
        ([_PageMarkdown(n - 1, f"page {n}") for n in (1, 2)], PageRange(start=5, end=9), "outside the document"),
    ],
)
def test_convert_reports_invalid_windows_as_typed_failures(
    pdf_inspector_stub: _Stub,
    pages: list[_PageMarkdown],
    page_range: PageRange | None,
    detail_fragment: str,
) -> None:
    pdf_inspector_stub.pages = pages
    result = _backend().convert(_request(page_range=page_range))
    assert result.pages == []
    failure = result.failures[0]
    assert failure.code is FailureCode.INVALID_INPUT
    assert detail_fragment in failure.detail
    assert failure.pass_kind is PassKind.NATIVE


def test_convert_pre_cancel_is_typed(pdf_inspector_stub: _Stub) -> None:
    pdf_inspector_stub.pages = [_PageMarkdown(0, "body")]
    result = _backend().convert(_request(cancellation=lambda: True))
    assert result.pages == []
    failure = result.failures[0]
    assert failure.code is FailureCode.CANCELLED
    assert failure.page_range is None
    assert pdf_inspector_stub.extract_pages_args == []  # nothing was extracted


def test_convert_cancels_between_pages(pdf_inspector_stub: _Stub) -> None:
    pdf_inspector_stub.pages = [_PageMarkdown(n - 1, f"page {n}") for n in (1, 2, 3)]
    calls = {"n": 0}

    def _cancel_after_first() -> bool:
        calls["n"] += 1
        return calls["n"] >= 3  # pre-check + page 1 → keep; page 2 check → stop

    result = _backend().convert(_request(cancellation=_cancel_after_first))
    assert [page.page_number for page in result.pages] == [1, 2, 3]  # requested pages stay present
    assert result.pages[0].blocks[0].content == "page 1"
    assert result.pages[1].blocks == []
    assert result.failures[0].code is FailureCode.CANCELLED


def test_convert_times_out_after_conversion(pdf_inspector_stub: _Stub) -> None:
    pdf_inspector_stub.pages = [_PageMarkdown(0, "body")]
    pdf_inspector_stub.extract_delay_s = 0.05
    result = _backend().convert(_request(timeout_s=0.01))
    assert result.pages == []
    failure = result.failures[0]
    assert failure.code is FailureCode.TIMEOUT
    assert failure.budget_s == 0.01


def test_convert_enforces_the_output_budget(pdf_inspector_stub: _Stub) -> None:
    pdf_inspector_stub.pages = [_PageMarkdown(0, "0123456789"), _PageMarkdown(1, "xxxxxxxxxx")]
    result = _backend().convert(_request(max_output_chars=10))
    assert [page.page_number for page in result.pages] == [1, 2]
    assert result.pages[0].blocks[0].content == "0123456789"
    assert result.pages[1].blocks == []
    assert result.failures[0].code is FailureCode.BUDGET_EXCEEDED


def test_convert_ties_invalid_pdf_input_to_invalid_input(pdf_inspector_stub: _Stub) -> None:
    pdf_inspector_stub.extract_error = ValueError("Not a PDF: file appears to be plain text")
    result = _backend().convert(_request())
    assert result.pages == []
    failure = result.failures[0]
    assert failure.code is FailureCode.INVALID_INPUT
    assert "plain text" in failure.detail


def test_convert_reports_library_errors_as_typed_failures(pdf_inspector_stub: _Stub) -> None:
    pdf_inspector_stub.extract_error = RuntimeError("pdfium exploded")
    result = _backend().convert(_request())
    assert result.pages == []
    failure = result.failures[0]
    assert failure.code is FailureCode.BACKEND_ERROR
    assert "RuntimeError: pdfium exploded" in failure.detail


def test_convert_reports_unreadable_files_as_a_typed_error(pdf_inspector_stub: _Stub) -> None:
    request = ConversionRequest(source=SourceDocument(uri="file:///definitely-not-here.pdf"))
    with pytest.raises(BackendError, match="cannot read source"):
        _backend().convert(request)


def test_source_bytes_reads_local_files_and_wraps_errors(tmp_path: Path, pdf_inspector_stub: _Stub) -> None:
    impl = _impl()
    target = tmp_path / "doc.pdf"
    target.write_bytes(b"%PDF-1.7 on disk")
    assert impl.source_bytes(SourceDocument(uri=target.absolute().as_uri())) == b"%PDF-1.7 on disk"
    with pytest.raises(BackendError, match="cannot read source"):
        impl.source_bytes(SourceDocument(uri=(tmp_path / "missing.pdf").absolute().as_uri()))


# ── Pipeline integration (routing → dispatch → IR) ──────────────────────────────


def test_pipeline_dispatches_pdf_inspector_with_the_liteparse_page_shape(pdf_inspector_stub: _Stub, monkeypatch: pytest.MonkeyPatch) -> None:
    pdf_inspector_stub.pages = [_PageMarkdown(0, "# Page one\n\nbody text\n"), _PageMarkdown(1, "# Page two\n\nmore text\n")]
    registry = _registry(monkeypatch)
    _register_pdf_inspector(registry)
    _add_fallback(registry)
    backend = registry.create("pdf-inspector", BackendConfig(name="pdf-inspector"))
    analysis = backend.analyze(_PDF_SOURCE)
    result = execute(analysis, registry, _constraints(), _PDF_SOURCE, produced_at=_PRODUCED)
    assert [group.winner for group in result.groups] == ["pdf-inspector"]
    assert result.groups[0].intent is Intent.NATIVE
    pages = result.document.pages
    assert [page.page_number for page in pages] == [1, 2]
    # One PARAGRAPH block per page carrying that page's Markdown — LiteParse's shape:
    assert all(len(page.blocks) == 1 for page in pages)
    assert [block.kind for page in pages for block in page.blocks] == [ChunkKind.PARAGRAPH, ChunkKind.PARAGRAPH]
    assert [block.id for page in pages for block in page.blocks] == ["pdf-inspector-p1-b0", "pdf-inspector-p2-b0"]
    assert all(block.metadata == {"backend": "pdf-inspector"} for page in pages for block in page.blocks)
    assert "# Page one" in to_markdown(result.document)


def test_pipeline_falls_back_when_pdf_inspector_rejects_the_source(pdf_inspector_stub: _Stub, monkeypatch: pytest.MonkeyPatch) -> None:
    pdf_inspector_stub.pages = [_PageMarkdown(0, "body")]
    registry = _registry(monkeypatch)
    _register_pdf_inspector(registry)
    _add_fallback(registry)
    analysis = _backend().analyze(_PDF_SOURCE)
    pdf_inspector_stub.extract_error = ValueError("Not a PDF: file is empty")
    result = execute(analysis, registry, _constraints(), _PDF_SOURCE, produced_at=_PRODUCED)
    group = result.groups[0]
    assert group.winner == "zzz-native"
    assert [attempt.backend for attempt in group.attempts] == ["pdf-inspector", "zzz-native"]
    assert group.attempts[0].failure is not None
    assert group.attempts[0].failure.code is FailureCode.INVALID_INPUT
    assert [page.blocks[0].content for page in result.document.pages] == ["fallback content 1"]


@pytest.fixture
def pdf_inspector_stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Stub]:
    """Install the stub package and force a fresh heavy-impl import against it."""
    stub = _Stub()
    _install_stub(stub, monkeypatch)
    monkeypatch.delitem(sys.modules, "parsecraft.backends.pdf_inspector._impl", raising=False)
    try:
        yield stub
    finally:
        sys.modules.pop("parsecraft.backends.pdf_inspector._impl", None)


def _install_stub(stub: _Stub, monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Register the fake package the heavy impl imports from."""
    module = types.ModuleType("pdf_inspector")
    module.PageMarkdown = _PageMarkdown  # ty: ignore[unresolved-attribute]

    def extract_pages_markdown_bytes(_data: bytes, pages: list[int] | None = None) -> _PagesExtractionResult:  # noqa: ANN202
        stub.extract_pages_args.append(pages)
        if stub.extract_error is not None:
            raise stub.extract_error
        if stub.extract_delay_s > 0:
            time.sleep(stub.extract_delay_s)
        selected = list(stub.pages) if pages is None else [page for page in stub.pages if page.page in pages]
        return _PagesExtractionResult(selected)

    module.extract_pages_markdown_bytes = extract_pages_markdown_bytes  # ty: ignore[unresolved-attribute]
    monkeypatch.setitem(sys.modules, "pdf_inspector", module)
    return module


def _registry(monkeypatch: pytest.MonkeyPatch) -> BackendRegistry:
    """Fresh registry with entry-point discovery stubbed out (offline, isolated)."""
    monkeypatch.setattr("parsecraft.backends.registry.entry_points", lambda **kwargs: [])
    return BackendRegistry()


def _register_pdf_inspector(registry: BackendRegistry) -> None:
    registry.register("pdf-inspector", backend_module.factory)


def _add_fallback(registry: BackendRegistry) -> None:
    """A name sorting after ``pdf-inspector`` so the plan prefers the real backend."""
    registry.register("zzz-native", _FallbackFactory())


def _constraints() -> RoutingConstraints:
    return RoutingConstraints(
        formats={"application/pdf"},
        installed_extras={"pdf-inspector"},
        vram_budget_gb=8.0,
        max_passes=3,
    )


def _backend() -> DocumentBackend:
    return backend_module.factory(BackendConfig(name="pdf-inspector"))


def _impl() -> types.ModuleType:
    return importlib.import_module("parsecraft.backends.pdf_inspector._impl")


def _request(
    *,
    page_range: PageRange | None = None,
    timeout_s: float | None = None,
    max_output_chars: int | None = None,
    cancellation: Callable[[], bool] | None = None,
) -> ConversionRequest:
    return ConversionRequest(
        source=_PDF_SOURCE,
        page_range=page_range,
        timeout_s=timeout_s,
        max_output_chars=max_output_chars,
        cancellation=cancellation,
    )
