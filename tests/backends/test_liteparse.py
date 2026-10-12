"""LiteParse backend: light/heavy boundary, verified formats, bound-checked convert.

Fully offline: the heavy ``liteparse`` import is satisfied by a stub module in
``sys.modules`` before ``_impl`` loads — same pattern as the OCR family.
"""

from __future__ import annotations

import hashlib
import importlib
import re
import sys
import time
from collections.abc import Callable, Iterator

import pytest

from parsecraft.backends import registry as registry_module
from parsecraft.backends.errors import BackendError, DependencyUnavailableError
from parsecraft.backends.liteparse import liteparse as liteparse_module
from parsecraft.backends.protocol import (
    BackendConfig,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
    SourceDocument,
)
from parsecraft.ir.models import FailureCode, PageRange, PassKind
from tests.fixtures.documents import document_bytes

_PDF_BYTES = document_bytes("pdf")
_PDF_SOURCE = SourceDocument(uri="file:///doc.pdf", content=_PDF_BYTES)


# ── Stubs: the heavy `liteparse` package, faked through sys.modules ─────────────


class _Stats:
    """Stand-in for liteparse.types.PageComplexityStats (fields the impl reads)."""

    def __init__(
        self,
        page_number: int,
        text_length: int,
        needs_ocr: bool,
        image_block_count: int = 0,
        is_garbled: bool = False,
    ) -> None:
        self.page_number = page_number
        self.text_length = text_length
        self.needs_ocr = needs_ocr
        self.image_block_count = image_block_count
        self.is_garbled = is_garbled


class _Page:
    def __init__(self, markdown: object, text: object = None) -> None:
        self.markdown = markdown
        self.text = text


class _Result:
    def __init__(self, pages: list[_Page]) -> None:
        self.pages = pages


class _ParserInstance:
    def __init__(self, state: _LiteparseStub, kwargs: dict[str, object]) -> None:
        self._state = state
        self.kwargs = kwargs

    def is_complex(self, _data: bytes) -> list[_Stats]:
        self._state.parser_calls.append(dict(self.kwargs))
        if self._state.is_complex_error is not None:
            raise self._state.is_complex_error
        return list(self._state.complexity)

    def parse(self, _data: bytes) -> _Result:
        self._state.parser_calls.append(dict(self.kwargs))
        target = self.kwargs.get("target_pages")
        page_number = int(target) if isinstance(target, str) else 1
        page_error = self._state.parse_errors_by_page.get(page_number)
        if page_error is not None:
            raise page_error
        if self._state.parse_error is not None:
            raise self._state.parse_error
        if self._state.parse_delay_s > 0:
            time.sleep(self._state.parse_delay_s)
        page = self._state.parse_by_page.get(page_number, self._state.default_page)
        if page is None:
            return _Result([])
        return _Result([page])


class _LiteparseStub:
    """Fake ``liteparse`` package: state scripted by each test."""

    def __init__(self) -> None:
        self.parser_calls: list[dict[str, object]] = []
        self.complexity: list[_Stats] = []
        self.parse_by_page: dict[int, _Page] = {}
        self.parse_errors_by_page: dict[int, Exception] = {}
        self.default_page: _Page | None = _Page("stub page")
        self.parse_error: Exception | None = None
        self.is_complex_error: Exception | None = None
        self.parse_delay_s = 0.0

    def LiteParse(self, **kwargs: object) -> _ParserInstance:  # noqa: N802 — mirrors the package API
        return _ParserInstance(self, kwargs)


# ── Light factory / descriptor (no heavy import anywhere) ────────────────────────


def test_descriptor_declares_only_verified_formats_and_stays_cpu_only() -> None:
    descriptor = liteparse_module.DESCRIPTOR
    assert descriptor.name == "liteparse"
    assert descriptor.version == liteparse_module.LITEPARSE_BACKEND_VERSION
    assert descriptor is liteparse_module.factory.descriptor
    capabilities = descriptor.capabilities
    # Verified against liteparse 2.14.7 — Office needs LibreOffice (undeclared),
    # `.html` is rejected upstream (undeclared):
    assert capabilities.supported_formats == ["application/pdf", "image/jpeg", "image/png", "image/tiff"]
    assert capabilities.gpu_requirement == 0.0
    assert capabilities.estimated_vram_gb is None
    assert capabilities.optional_dependency_group == "liteparse"
    assert capabilities.model_asset is None
    assert capabilities.supports_page_ranges is True
    assert capabilities.supports_multi_page is True


def test_entry_point_module_is_registry_ready() -> None:
    registry = registry_module.BackendRegistry()
    registry._entry_points_loaded = True  # isolate from installed entry points
    registry.register("liteparse", liteparse_module.factory)
    assert registry.get("liteparse").name == "liteparse"
    assert [d.name for d in registry.list_backends()] == ["liteparse"]


def test_missing_extra_error_names_parsecraft_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = importlib.import_module

    def _fake_import(module_name: str, *_args: object, **_kwargs: object) -> object:
        if module_name.startswith("parsecraft.backends.liteparse."):
            msg = f"No module named {module_name!r}"
            raise ModuleNotFoundError(msg, name="liteparse")
        return real_import(module_name)

    monkeypatch.setattr(importlib, "import_module", _fake_import)
    with pytest.raises(
        DependencyUnavailableError,
        match=re.escape("backend dependency 'liteparse' is not installed — install the 'liteparse' extra"),
    ) as excinfo:
        liteparse_module.factory(BackendConfig(name="liteparse"))
    assert (excinfo.value.module, excinfo.value.extra) == ("liteparse", "liteparse")
    assert isinstance(excinfo.value, BackendError)


def test_impl_without_create_entry_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib, "import_module", lambda _name: object())
    with pytest.raises(BackendError, match="must expose create"):
        liteparse_module.factory(BackendConfig(name="liteparse"))


# ── Heavy impl under the stub ─────────────────────────────────────────────────────


def test_factory_returns_the_impl_backend(liteparse_stub: _LiteparseStub) -> None:
    backend = _backend()
    assert backend.name == "liteparse"
    assert backend.capabilities is liteparse_module.DESCRIPTOR.capabilities


def test_analyze_maps_complexity_stats_onto_page_signals(liteparse_stub: _LiteparseStub) -> None:
    liteparse_stub.complexity = [
        _Stats(1, text_length=364, needs_ocr=True, image_block_count=2),
        _Stats(2, text_length=5000, needs_ocr=False),
        _Stats(3, text_length=0, needs_ocr=True, is_garbled=True),
    ]
    backend = _backend()
    analysis = backend.analyze(_PDF_SOURCE)
    assert analysis.page_count == 3
    assert analysis.source_hash == hashlib.sha256(_PDF_BYTES).hexdigest()
    first, second, third = analysis.signals
    assert (first.has_native_text, first.text_chars, first.image_count, first.blank) == (False, 364, 2, False)
    assert (second.has_native_text, second.text_chars, second.image_count) == (True, 5000, 0)
    assert second.replacement_char_ratio is None
    assert third.replacement_char_ratio == 1.0  # is_garbled → maximum ratio
    # Cheap pass, never OCR-enabled internally:
    assert liteparse_stub.parser_calls[0]["ocr_enabled"] is False
    assert liteparse_stub.parser_calls[0]["quiet"] is True


def test_analyze_wraps_inspection_failures_in_a_typed_error(liteparse_stub: _LiteparseStub) -> None:
    liteparse_stub.is_complex_error = RuntimeError("broken document")
    with pytest.raises(BackendError, match="liteparse cannot inspect source"):
        _backend().analyze(_PDF_SOURCE)


def test_convert_builds_typed_ir_per_page(liteparse_stub: _LiteparseStub) -> None:
    liteparse_stub.complexity = [_Stats(1, 10, False), _Stats(2, 10, False)]
    liteparse_stub.parse_by_page = {1: _Page("# Markdown page"), 2: _Page(None, "plain fallback")}
    result = _convert(_backend())
    assert result.failures == []
    assert [page.page_number for page in result.pages] == [1, 2]
    assert result.pages[0].blocks[0].content == "# Markdown page"
    assert result.pages[1].blocks[0].content == "plain fallback"  # markdown → text fallback
    assert result.pages[0].blocks[0].id == "liteparse-p1-b0"
    assert result.backend.name == "liteparse"
    assert result.backend.version == liteparse_module.LITEPARSE_BACKEND_VERSION
    targets = [call.get("target_pages") for call in liteparse_stub.parser_calls if "target_pages" in call]
    assert targets == ["1", "2"]  # one bound-checked parse per page (analyze used the count pass)


def test_convert_honors_the_page_range(liteparse_stub: _LiteparseStub) -> None:
    liteparse_stub.complexity = [_Stats(n, 10, False) for n in (1, 2, 3)]
    liteparse_stub.parse_by_page = {n: _Page(f"page {n}") for n in (1, 2, 3)}
    result = _convert(_backend(), page_range=PageRange(start=2, end=3))
    assert [page.page_number for page in result.pages] == [2, 3]
    targets = [call.get("target_pages") for call in liteparse_stub.parser_calls if "target_pages" in call]
    assert targets == ["2", "3"]


@pytest.mark.parametrize(
    ("complexity", "page_range", "detail_fragment"),
    [
        ([], None, "no pages"),
        ([_Stats(1, 10, False), _Stats(2, 10, False)], PageRange(start=5, end=9), "outside the document"),
    ],
)
def test_convert_reports_invalid_windows_as_typed_failures(
    liteparse_stub: _LiteparseStub,
    complexity: list[_Stats],
    page_range: PageRange | None,
    detail_fragment: str,
) -> None:
    liteparse_stub.complexity = complexity
    result = _convert(_backend(), page_range=page_range)
    assert result.pages == []
    failure = result.failures[0]
    assert failure.code is FailureCode.INVALID_INPUT
    assert detail_fragment in failure.detail
    assert failure.pass_kind is PassKind.NATIVE


def test_convert_stops_on_pre_cancel_and_between_pages(liteparse_stub: _LiteparseStub) -> None:
    liteparse_stub.complexity = [_Stats(n, 10, False) for n in (1, 2, 3)]
    liteparse_stub.parse_by_page = {n: _Page(f"page {n}") for n in (1, 2, 3)}

    pre_cancel = _convert(_backend(), cancellation=lambda: True)
    assert pre_cancel.pages == []
    assert pre_cancel.failures[0].code is FailureCode.CANCELLED

    calls = {"n": 0}

    def _cancel_after_first() -> bool:
        calls["n"] += 1
        return calls["n"] >= 3  # pre-check + page 1 → keep; page 2 check → stop

    between = _convert(_backend(), cancellation=_cancel_after_first)
    assert [page.page_number for page in between.pages] == [1]
    assert between.failures[0].code is FailureCode.CANCELLED


def test_convert_times_out_between_pages(liteparse_stub: _LiteparseStub) -> None:
    liteparse_stub.complexity = [_Stats(n, 10, False) for n in (1, 2, 3)]
    liteparse_stub.parse_by_page = {n: _Page(f"page {n}") for n in (1, 2, 3)}
    liteparse_stub.parse_delay_s = 0.08  # page 1 alone blows the 50 ms budget
    result = _convert(_backend(), timeout_s=0.05)
    assert [page.page_number for page in result.pages] == [1]
    failure = result.failures[0]
    assert failure.code is FailureCode.TIMEOUT
    assert failure.budget_s == 0.05


def test_convert_enforces_the_output_budget(liteparse_stub: _LiteparseStub) -> None:
    liteparse_stub.complexity = [_Stats(n, 10, False) for n in (1, 2)]
    liteparse_stub.parse_by_page = {1: _Page("0123456789"), 2: _Page("xxxxxxxxxx")}
    result = _convert(_backend(), max_output_chars=15)
    assert [page.page_number for page in result.pages] == [1]
    assert result.failures[0].code is FailureCode.BUDGET_EXCEEDED


def test_convert_records_per_page_errors_and_continues(liteparse_stub: _LiteparseStub) -> None:
    liteparse_stub.complexity = [_Stats(n, 10, False) for n in (1, 2, 3)]
    liteparse_stub.parse_by_page = {1: _Page("one"), 3: _Page("three")}

    class _Boom(Exception):
        pass

    liteparse_stub.parse_errors_by_page[2] = _Boom("pdfium rejected page 2")
    result = _convert(_backend())
    assert [page.page_number for page in result.pages] == [1, 3]
    failure = result.failures[0]
    assert failure.code is FailureCode.BACKEND_ERROR
    assert "page 2: _Boom: pdfium rejected page 2" in failure.detail
    assert failure.page_range == PageRange(start=2, end=2)


def test_convert_ties_unreadable_sources_to_invalid_input(liteparse_stub: _LiteparseStub) -> None:
    liteparse_stub.is_complex_error = ValueError("unsupported file format: .txt")
    result = _convert(_backend())
    assert result.pages == []
    assert result.failures[0].code is FailureCode.INVALID_INPUT
    assert "unsupported file format" in result.failures[0].detail


def test_convert_rejects_remote_sources_with_a_typed_error(liteparse_stub: _LiteparseStub) -> None:
    request = ConversionRequest(source=SourceDocument(uri="https://example.com/doc.pdf"))
    with pytest.raises(BackendError, match="must carry in-memory content"):
        _backend().convert(request)


def test_convert_reports_unreadable_files_as_a_typed_error(liteparse_stub: _LiteparseStub) -> None:
    request = ConversionRequest(source=SourceDocument(uri="file:///definitely-not-here.pdf"))
    with pytest.raises(BackendError, match="cannot read source"):
        _backend().convert(request)


def test_convert_surfaces_pages_liteparse_skipped(liteparse_stub: _LiteparseStub) -> None:
    liteparse_stub.complexity = [_Stats(1, 10, False)]
    liteparse_stub.default_page = None  # parse returns no pages for the request
    result = _convert(_backend())
    assert result.pages == []
    failure = result.failures[0]
    assert failure.code is FailureCode.BACKEND_ERROR
    assert "returned no content for page 1" in failure.detail


def test_convert_ties_non_string_page_content_to_a_typed_failure(liteparse_stub: _LiteparseStub) -> None:
    liteparse_stub.complexity = [_Stats(1, 10, False)]
    liteparse_stub.parse_by_page = {1: _Page(markdown=123)}
    result = _convert(_backend())
    assert result.pages == []
    failure = result.failures[0]
    assert failure.code is FailureCode.BACKEND_ERROR
    assert "unexpected page content type" in failure.detail


def _convert(
    backend: DocumentBackend,
    *,
    page_range: PageRange | None = None,
    timeout_s: float | None = None,
    cancellation: Callable[[], bool] | None = None,
    max_output_chars: int | None = None,
) -> BackendResult:
    request = ConversionRequest(
        source=_PDF_SOURCE,
        page_range=page_range,
        timeout_s=timeout_s,
        cancellation=cancellation,
        max_output_chars=max_output_chars,
    )
    return backend.convert(request)


@pytest.fixture
def liteparse_stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[_LiteparseStub]:
    """Install the fake package and force a fresh heavy-impl import against it."""
    stub = _LiteparseStub()
    monkeypatch.setitem(sys.modules, "liteparse", stub)
    sys.modules.pop("parsecraft.backends.liteparse._impl", None)
    try:
        yield stub
    finally:
        sys.modules.pop("parsecraft.backends.liteparse._impl", None)


def _backend(config: BackendConfig | None = None) -> DocumentBackend:
    return liteparse_module.factory(config or BackendConfig(name="liteparse"))
