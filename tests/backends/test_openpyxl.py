"""Tests for the openpyxl backend — typed TABLE chunks from a workbook grid.

The light env carries no openpyxl (it is the backend's own extra), so the grid
and failure paths run against a fake ``openpyxl`` module installed in
``sys.modules`` before the impl is imported; the real engine is exercised by one
extra-gated test that builds a workbook with openpyxl itself.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from importlib import import_module
from types import ModuleType
from typing import Any

import pytest

from parsecraft.backends.errors import BackendError, DependencyUnavailableError
from parsecraft.backends.openpyxl import openpyxl as openpyxl_module
from parsecraft.backends.protocol import BackendConfig, ConversionRequest, SourceDocument
from parsecraft.ir.models import ChunkKind, FailureCode, PageRange
from tests.fixtures.documents import XLSX_SHEET, XLSX_TEXTS, xlsx_bytes

_IMPL_MODULE = "parsecraft.backends.openpyxl._impl"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class _FakeSheet:
    """A worksheet: a title and a grid of stored values."""

    def __init__(self, title: str, rows: list[tuple[object, ...]]) -> None:
        self.title = title
        self._rows = rows

    def iter_rows(self, *, values_only: bool = False) -> Iterator[tuple[object, ...]]:
        assert values_only is True, "the backend must read values, not cell objects"
        return iter(self._rows)


class _FakeWorkbook:
    """A workbook: its worksheets plus the close() bookkeeping the backend owes."""

    def __init__(self, sheets: list[_FakeSheet]) -> None:
        self.worksheets = sheets
        self.closed = False

    def close(self) -> None:
        self.closed = True


# ── descriptor + factory ───────────────────────────────────────────────────


def test_openpyxl_descriptor_contract() -> None:
    descriptor = openpyxl_module.factory.descriptor
    assert descriptor.name == "openpyxl"
    assert descriptor.capabilities.supported_formats == [_XLSX_MIME]
    assert descriptor.capabilities.optional_dependency_group == "openpyxl"
    assert descriptor.capabilities.convert_extra == "openpyxl"
    assert descriptor.capabilities.supports_page_ranges is False
    assert descriptor.capabilities.supports_multi_page is False
    assert descriptor.capabilities.estimated_vram_gb is None
    assert descriptor.capabilities.model_asset is None


def test_openpyxl_factory_reports_the_missing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """A light-env install names the extra instead of failing with ImportError."""
    monkeypatch.setattr(
        "parsecraft.backends.openpyxl.openpyxl._IMPL_MODULE",
        "parsecraft.backends.openpyxl._not_installed",
    )
    with pytest.raises(DependencyUnavailableError, match="openpyxl"):
        openpyxl_module.factory(BackendConfig(name="openpyxl"))


def test_openpyxl_factory_resolves_the_impl_when_the_extra_is_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """The factory is the sanctioned heavy boundary: it returns a working backend."""
    _install_engine(monkeypatch, _FakeWorkbook([_FakeSheet("S", [("a",)])]), [])
    backend = openpyxl_module.factory(BackendConfig(name="openpyxl"))

    assert backend.capabilities.supported_formats == [_XLSX_MIME]
    assert backend.convert(_request()).failures == []


def test_openpyxl_factory_rejects_an_impl_without_create(monkeypatch: pytest.MonkeyPatch) -> None:
    """An importable module that is not an impl module is a contract error, not a silent pass."""
    monkeypatch.setattr(
        "parsecraft.backends.openpyxl.openpyxl._IMPL_MODULE",
        "parsecraft.backends.openpyxl.openpyxl",  # importable, but exposes no create()
    )
    with pytest.raises(BackendError, match="must expose create"):
        openpyxl_module.factory(BackendConfig(name="openpyxl"))


# ── analyze: the size proxy ────────────────────────────────────────────────


def test_openpyxl_analyze_is_a_size_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _backend(monkeypatch, _FakeWorkbook([]), [])
    data = b"x" * 32
    analysis = backend.analyze(_source(data))

    assert analysis.page_count == 1
    (signal,) = analysis.signals
    assert signal.text_chars == len(data)
    assert signal.blank is False
    assert signal.image_count == 0
    assert signal.replacement_char_ratio is None


# ── convert: typed tables ──────────────────────────────────────────────────


def test_openpyxl_types_each_sheet_as_a_table(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    sheet = _FakeSheet("Sheet One", [("Region", "Score"), ("north", 0.5)])
    workbook = _FakeWorkbook([sheet])
    result = _backend(monkeypatch, workbook, calls).convert(_request())

    assert calls == [{"read_only": True, "data_only": True}]  # no formula evaluation
    assert workbook.closed is True
    assert result.failures == []
    (page,) = result.pages
    assert page.page_number == 1
    (chunk,) = page.blocks
    assert chunk.kind is ChunkKind.TABLE
    assert chunk.rows == (("Region", "Score"), ("north", "0.5"))  # numbers become text
    assert chunk.metadata == {"backend": "openpyxl", "sheet": "Sheet One"}
    assert chunk.content == "| Region | Score |\n| --- | --- |\n| north | 0.5 |"


def test_openpyxl_gives_every_sheet_its_own_table(monkeypatch: pytest.MonkeyPatch) -> None:
    sheets = [_FakeSheet("First", [("a",)]), _FakeSheet("Second", [("b",)])]
    result = _backend(monkeypatch, _FakeWorkbook(sheets), []).convert(_request())

    (page,) = result.pages
    assert [chunk.metadata["sheet"] for chunk in page.blocks] == ["First", "Second"]
    assert [chunk.reading_order for chunk in page.blocks] == [0, 1]
    assert page.blocks[1].content == "| b |\n| --- |"


def test_openpyxl_trims_trailing_blanks_and_keeps_interior_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    sheet = _FakeSheet("S", [("h1", "h2", None), ("x", None, "z"), (None, None, None)])
    result = _backend(monkeypatch, _FakeWorkbook([sheet]), []).convert(_request())

    (chunk,) = result.pages[0].blocks
    # trailing blank row dropped; column 3 survives because a later row fills it,
    # and the interior blank is kept — dropping it would shift columns.
    assert chunk.rows == (("h1", "h2", ""), ("x", "", "z"))


def test_openpyxl_empty_sheet_keeps_its_table_but_carries_no_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _backend(monkeypatch, _FakeWorkbook([_FakeSheet("Empty", [])]), []).convert(_request())

    (chunk,) = result.pages[0].blocks
    assert chunk.kind is ChunkKind.TABLE
    assert chunk.rows == ()
    assert chunk.content == ""
    assert chunk.metadata["sheet"] == "Empty"


# ── convert: typed failures ─────────────────────────────────────────────────


def test_openpyxl_rejects_a_foreign_media_type(monkeypatch: pytest.MonkeyPatch) -> None:
    request = ConversionRequest(source=_source(media_type="application/pdf"))
    result = _backend(monkeypatch, _FakeWorkbook([]), []).convert(request)

    assert result.pages == []
    assert result.failures[0].code is FailureCode.INVALID_INPUT


def test_openpyxl_range_beyond_page_one_is_invalid_input(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _backend(monkeypatch, _FakeWorkbook([]), []).convert(_request(page_range=PageRange(start=2, end=3)))

    assert result.failures[0].code is FailureCode.INVALID_INPUT
    assert "single page" in result.failures[0].detail


def test_openpyxl_cancellation_is_recorded_before_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    result = _backend(monkeypatch, _FakeWorkbook([]), calls).convert(_request(cancellation=lambda: True))

    assert result.failures[0].code is FailureCode.CANCELLED
    assert calls == []  # nothing was loaded


def test_openpyxl_exhausted_deadline_is_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _backend(monkeypatch, _FakeWorkbook([]), []).convert(_request(timeout_s=1e-06))

    assert result.failures[0].code is FailureCode.TIMEOUT


def test_openpyxl_budget_overflow_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    sheets = [_FakeSheet("S", [("a", "b")])]
    result = _backend(monkeypatch, _FakeWorkbook(sheets), []).convert(_request(max_output_chars=1))

    assert result.pages == []
    assert result.failures[0].code is FailureCode.BUDGET_EXCEEDED


def test_openpyxl_load_failure_is_a_typed_backend_error(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _backend(monkeypatch, ValueError("not a workbook"), []).convert(_request())

    assert result.pages == []
    failure = result.failures[0]
    assert failure.code is FailureCode.BACKEND_ERROR
    assert "ValueError" in failure.detail


def _request(content: bytes = b"workbook", **kwargs: Any) -> ConversionRequest:
    return ConversionRequest(source=_source(content), **kwargs)


def _backend(monkeypatch: pytest.MonkeyPatch, outcome: object, calls: list[dict[str, object]]) -> Any:
    impl = _install_engine(monkeypatch, outcome, calls)
    return impl.create(BackendConfig(name="openpyxl"))


def _install_engine(monkeypatch: pytest.MonkeyPatch, outcome: object, calls: list[dict[str, object]]) -> ModuleType:
    """Fake ``openpyxl`` + a freshly imported impl, returning the impl module.

    ``outcome`` is the workbook to hand back, or the exception ``load_workbook``
    should raise. Keyword arguments are recorded so a test can pin that the
    backend reads without evaluating formulas.
    """

    def load_workbook(*args: object, **kwargs: object) -> object:
        calls.append(kwargs)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setitem(sys.modules, "openpyxl", _stub_module("openpyxl", load_workbook=load_workbook))
    sys.modules.pop(_IMPL_MODULE, None)
    return import_module(_IMPL_MODULE)


def _stub_module(name: str, **attributes: object) -> ModuleType:
    """A module whose attributes are set through ``__dict__`` (ruff- and ty-stable)."""
    module = ModuleType(name)
    module.__dict__.update(attributes)
    return module


# ── the real engine (extra-gated) ───────────────────────────────────────────


def test_real_openpyxl_converts_a_generated_workbook() -> None:
    """End-to-end with the real engine and a real, generated workbook.

    Skipped without the ``openpyxl`` extra; run it isolated:
    ``uv run --isolated --extra openpyxl pytest tests/backends/test_openpyxl.py -k real_openpyxl``.
    """
    pytest.importorskip("openpyxl", reason="the openpyxl extra is not installed")
    sys.modules.pop(_IMPL_MODULE, None)
    try:
        backend = import_module(_IMPL_MODULE).create(BackendConfig(name="openpyxl"))
        content = xlsx_bytes()
        assert xlsx_bytes() == content, "the workbook scaffold must be byte-deterministic"
        result = backend.convert(ConversionRequest(source=_source(content)))

        assert result.failures == []
        (page,) = result.pages
        (chunk,) = page.blocks
        assert chunk.kind is ChunkKind.TABLE
        assert chunk.metadata["sheet"] == XLSX_SHEET
        grid = "\n".join("\t".join(row) for row in chunk.rows or ())
        for text in XLSX_TEXTS:
            assert text in grid, f"{text!r} missing from the typed grid"
    finally:
        sys.modules.pop(_IMPL_MODULE, None)


def _source(content: bytes = b"workbook", media_type: str | None = _XLSX_MIME) -> SourceDocument:
    return SourceDocument(uri="mem://openpyxl", content=content, media_type=media_type)
