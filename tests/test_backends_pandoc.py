"""Offline tests for the pandoc backend (binary + wrapper stubbed)."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from parsecraft.backends.errors import BackendError, DependencyUnavailableError
from parsecraft.backends.pandoc.pandoc import (
    DESCRIPTOR,
    PANDOC_FORMATS,
    PANDOC_READERS,
    factory,
)
from parsecraft.backends.protocol import BackendConfig, ConversionRequest, SourceDocument
from parsecraft.ir.models import ChunkKind, FailureCode, PageRange

_CONFIG = BackendConfig(name="pandoc")


# ── descriptor: only verified formats ──────────────────────────────────────


def test_descriptor_declares_exactly_the_verified_formats() -> None:
    assert DESCRIPTOR.name == "pandoc"
    assert DESCRIPTOR.version == "0.1.0"
    assert set(DESCRIPTOR.capabilities.supported_formats) == set(PANDOC_FORMATS)
    # pandoc 3.11 has no ods/odp reader — never declared (verification rule)
    assert "application/vnd.oasis.opendocument.spreadsheet" not in PANDOC_FORMATS
    assert "application/vnd.oasis.opendocument.presentation" not in PANDOC_FORMATS
    assert set(PANDOC_READERS) == set(PANDOC_FORMATS)
    capabilities = DESCRIPTOR.capabilities
    assert capabilities.supports_page_ranges is False
    assert capabilities.supports_multi_page is False
    assert capabilities.optional_dependency_group == "pandoc"
    assert capabilities.gpu_requirement == 0.0
    assert capabilities.model_asset is None


# ── light factory: typed boundary errors ───────────────────────────────────


def test_factory_missing_extra_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        raise ImportError("No module named 'pypandoc'", name="pypandoc")

    monkeypatch.setattr(importlib, "import_module", fake_import)
    with pytest.raises(DependencyUnavailableError) as exc_info:
        factory(_CONFIG)
    assert exc_info.value.module == "pypandoc"
    assert exc_info.value.extra == "pandoc"


def test_factory_rejects_impl_without_create(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib, "import_module", lambda name: ModuleType("bare.impl"))
    with pytest.raises(BackendError, match="must expose create"):
        factory(_CONFIG)


def test_missing_binary_is_typed_at_instantiation(monkeypatch: pytest.MonkeyPatch) -> None:
    # No fixture: install only the failing stub (binary missing at create()).
    monkeypatch.setitem(sys.modules, "pypandoc", make_fake_pypandoc({}, version_error=True))
    sys.modules.pop("parsecraft.backends.pandoc._impl", None)
    with pytest.raises(DependencyUnavailableError) as exc_info:
        factory(_CONFIG)
    assert exc_info.value.module == "pandoc"  # the system binary, not the wrapper
    sys.modules.pop("parsecraft.backends.pandoc._impl", None)


# ── analyze: cheap size proxy ──────────────────────────────────────────────


def test_analyze_reports_single_text_bearing_page(pypandoc_stub: dict[str, Any]) -> None:
    backend = factory(_CONFIG)
    payload = b"PK\x03\x04office container bytes"
    analysis = backend.analyze(_source(payload))
    assert analysis.page_count == 1
    signal = analysis.signals[0]
    assert signal.has_native_text is True
    assert signal.blank is False
    assert signal.text_chars == len(payload)  # documented size proxy
    assert signal.replacement_char_ratio is None


def test_analyze_reads_file_uri(tmp_path: Path, pypandoc_stub: dict[str, Any]) -> None:
    path = tmp_path / "doc.docx"
    path.write_bytes(b"on disk")
    backend = factory(_CONFIG)
    analysis = backend.analyze(SourceDocument(uri=f"file://{path}", media_type=None, content=None))
    assert analysis.signals[0].text_chars == len(b"on disk")


# ── convert: bounds and typed failures ─────────────────────────────────────


def test_convert_single_page_happy_path(pypandoc_stub: dict[str, Any]) -> None:
    backend = factory(_CONFIG)
    result = backend.convert(make_request())
    assert result.failures == []
    assert len(result.pages) == 1
    block = result.pages[0].blocks[0]
    assert block.id == "pandoc-p1-b0"
    assert block.kind is ChunkKind.PARAGRAPH
    assert block.metadata == {"backend": "pandoc"}
    # the fake recorded the verified reader mapping
    assert pypandoc_stub["format"] == "docx"
    assert pypandoc_stub["to"] == "gfm"
    assert pypandoc_stub["src"] == b"PK\x03\x04fake-office-bytes"


def test_convert_unsupported_media_type_is_typed(pypandoc_stub: dict[str, Any]) -> None:
    backend = factory(_CONFIG)
    result = backend.convert(make_request(media_type="text/plain"))
    assert result.pages == []
    assert [f.code for f in result.failures] == [FailureCode.INVALID_INPUT]
    assert "no verified reader" in result.failures[0].detail
    assert pypandoc_stub == {}  # conversion never attempted


def test_convert_page_range_beyond_page_one_is_typed(pypandoc_stub: dict[str, Any]) -> None:

    backend = factory(_CONFIG)
    result = backend.convert(make_request(page_range=PageRange(start=2, end=3)))
    assert [f.code for f in result.failures] == [FailureCode.INVALID_INPUT]
    assert "single-page" in result.failures[0].detail


def test_convert_cancellation_before_extraction(pypandoc_stub: dict[str, Any]) -> None:
    backend = factory(_CONFIG)
    result = backend.convert(make_request(cancellation=lambda: True))
    assert [f.code for f in result.failures] == [FailureCode.CANCELLED]
    assert result.pages == []


def test_convert_timeout_before_conversion(monkeypatch: pytest.MonkeyPatch, pypandoc_stub: dict[str, Any]) -> None:
    times = iter([0.0, 100.0, 100.0, 100.0])
    impl_module = importlib.import_module("parsecraft.backends.pandoc._impl")
    assert impl_module.monotonic is not None
    monkeypatch.setattr(impl_module, "monotonic", lambda: next(times))
    backend = factory(_CONFIG)
    result = backend.convert(make_request(timeout_s=1.0))
    assert [f.code for f in result.failures] == [FailureCode.TIMEOUT]
    assert result.failures[0].budget_s == 1.0


@pytest.fixture
def pypandoc_stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """Install a fake `pypandoc` and force a fresh heavy-impl import (liteparse pattern)."""
    records: dict[str, Any] = {}
    monkeypatch.setitem(sys.modules, "pypandoc", make_fake_pypandoc(records))
    sys.modules.pop("parsecraft.backends.pandoc._impl", None)
    yield records
    sys.modules.pop("parsecraft.backends.pandoc._impl", None)


def test_convert_budget_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pypandoc", make_fake_pypandoc({}, markdown="x" * 50))
    sys.modules.pop("parsecraft.backends.pandoc._impl", None)
    backend = factory(_CONFIG)
    result = backend.convert(make_request(max_output_chars=10))
    assert [f.code for f in result.failures] == [FailureCode.BUDGET_EXCEEDED]
    assert "50 produced" in result.failures[0].detail


def test_convert_failure_is_typed_when_pandoc_rejects_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pypandoc", make_fake_pypandoc({}, convert_error=OSError("docx zip is corrupt")))
    sys.modules.pop("parsecraft.backends.pandoc._impl", None)
    backend = factory(_CONFIG)
    result = backend.convert(make_request())
    assert [f.code for f in result.failures] == [FailureCode.BACKEND_ERROR]
    assert "docx zip is corrupt" in result.failures[0].detail


def make_fake_pypandoc(
    records: dict[str, Any],
    *,
    markdown: str = "converted markdown",
    version_error: bool = False,
    convert_error: Exception | None = None,
) -> ModuleType:
    stub = ModuleType("pypandoc")

    def get_pandoc_version() -> str:
        if version_error:
            raise OSError("No pandoc was found")
        return "3.11"

    def convert_text(src: Any, *, to: str, format: str, **kwargs: Any) -> str:
        records.update({"src": src, "to": to, "format": format})
        if convert_error is not None:
            raise convert_error
        return markdown

    stub.get_pandoc_version = get_pandoc_version  # ty: ignore[unresolved-attribute] — fake module
    stub.convert_text = convert_text  # ty: ignore[unresolved-attribute] — fake module
    return stub


def test_source_bytes_rejects_non_file_uri() -> None:
    backend = factory(_CONFIG)
    with pytest.raises(BackendError, match="only file:// URIs are read from disk"):
        backend.analyze(_source(None, uri="https://example.test/doc.docx"))


def test_source_bytes_missing_local_file_is_typed(tmp_path: Path) -> None:
    backend = factory(_CONFIG)
    missing = tmp_path / "absent.docx"
    with pytest.raises(BackendError, match="cannot read source"):
        backend.analyze(_source(None, uri=f"file://{missing}"))


def _source(
    content: bytes | None,
    *,
    media_type: str | None = "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    uri: str = "mem://doc.docx",
) -> SourceDocument:
    return SourceDocument(uri=uri, media_type=media_type, content=content)


# ── real-binary integration (active only with the `pandoc` extra) ──────────


def test_real_pandoc_docx_round_trip(tmp_path: Path) -> None:
    pypandoc = pytest.importorskip("pypandoc", reason="pandoc extra not installed")
    pypandoc.get_pandoc_version()  # skips nothing; raises if the binary is missing
    docx = tmp_path / "sample.docx"
    pypandoc.convert_text(
        "# Hello pandoc\n\nReal round-trip body.",
        to="docx",
        format="markdown",
        outputfile=str(docx),
    )
    sys.modules.pop("parsecraft.backends.pandoc._impl", None)
    backend = factory(_CONFIG)
    result = backend.convert(make_request(docx.read_bytes(), uri=f"file://{docx}"))
    assert result.failures == []
    content = result.pages[0].blocks[0].content
    assert "Hello pandoc" in content
    assert "Real round-trip body." in content
    sys.modules.pop("parsecraft.backends.pandoc._impl", None)


def make_request(
    content: bytes | None = b"PK\x03\x04fake-office-bytes",
    *,
    media_type: str | None = "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    uri: str = "mem://doc.docx",
    **kwargs: Any,
) -> ConversionRequest:
    source = SourceDocument(uri=uri, media_type=media_type, content=content)
    return ConversionRequest(source=source, **kwargs)
