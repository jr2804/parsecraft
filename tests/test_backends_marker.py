"""Tests for the Marker backend (``parsecraft.backends.marker``).

The heavy ``marker`` package is **not** installed in CI (ADR-0006: dependency
conflicts with the shared vllm / torch window on 3.14). Instead, ``marker`` is
stubbed via ``sys.modules`` — the same technique the pandoc and docling test
suites use — so the light factory, ``analyze()``, ``convert()``, and every
error path are exercised without the real dependency graph.

Coverage map:
- Descriptor shape and factory wiring → gh-1 acceptance criteria.
- analyze() returns a minimal one-page signal (converter-only, ADR-0006 d3).
- convert() maps paginated marker markdown → per-page IR with 1-based pages.
- convert() records a typed ``INVALID_INPUT`` pass-failure for non-PDF sources
  (no dedicated exception: descriptors declare formats, failures are records).
- convert() returns DEPENDENCY_MISSING when marker-pdf is absent or models
  cannot be fetched (graceful fallback per gh-1).
- convert() honours page-range, output budget, cancellation, and timeout.
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import Protocol, cast
from unittest.mock import MagicMock

import pytest

import parsecraft.backends.marker.marker
from parsecraft.backends.errors import (
    BackendError,
    DependencyUnavailableError,
)
from parsecraft.backends.marker.marker import (
    DESCRIPTOR,
    MARKER_BACKEND_VERSION,
    MARKER_FORMATS,
    factory,
)
from parsecraft.backends.protocol import (
    BackendConfig,
    ConversionRequest,
    SourceDocument,
)
from parsecraft.ir.models import (
    ChunkKind,
    FailureCode,
    PageRange,
    PageResult,
)

_PAGE_SEP = "-" * 48  # marker's default page_separator
_IMPL_MODULE = "parsecraft.backends.marker._impl"


# ── Fixtures ──

_CONFIG = BackendConfig(name="test")
_PDF_SOURCE = SourceDocument(
    uri="file:///test.pdf",
    media_type="application/pdf",
    content=b"%PDF-1.4 fake",
)
_PDF_SOURCE_NO_CONTENT = SourceDocument(
    uri="file:///test.pdf",
    media_type="application/pdf",
    content=None,
)
_HTML_SOURCE = SourceDocument(
    uri="file:///test.html",
    media_type="text/html",
    content=b"<html></html>",
)


# ── Descriptor & factory ──


def test_descriptor_has_expected_shape() -> None:
    assert DESCRIPTOR.name == "marker"
    assert DESCRIPTOR.version == MARKER_BACKEND_VERSION
    assert DESCRIPTOR.capabilities.supported_formats == list(MARKER_FORMATS)
    assert DESCRIPTOR.capabilities.supported_formats == ["application/pdf"]
    assert DESCRIPTOR.capabilities.supports_page_ranges is True
    assert DESCRIPTOR.capabilities.supports_multi_page is True
    assert DESCRIPTOR.capabilities.optional_dependency_group == "marker"


def test_factory_descriptor_matches() -> None:
    assert factory.descriptor is DESCRIPTOR


def test_factory_raises_dependency_unavailable_when_marker_missing(stub_marker_no_extra: None) -> None:
    """Without marker-pdf installed the factory raises DependencyUnavailableError."""
    with pytest.raises(DependencyUnavailableError) as exc:
        factory(_CONFIG)
    assert exc.value.extra == "marker"
    assert "marker" in exc.value.module


@pytest.fixture
def stub_marker_no_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure no `marker` modules are in sys.modules."""
    for key in [k for k in sys.modules if k == "marker" or k.startswith("marker.")]:
        monkeypatch.delitem(sys.modules, key, raising=False)
    monkeypatch.delitem(sys.modules, _IMPL_MODULE, raising=False)


def test_factory_raises_backend_error_when_create_missing(stub_marker: type, monkeypatch: pytest.MonkeyPatch) -> None:
    """If _impl has no create() the factory raises BackendError."""
    impl = importlib.import_module(_IMPL_MODULE)
    monkeypatch.delattr(impl, "create", raising=False)
    with pytest.raises(BackendError, match="must expose create"):
        factory(_CONFIG)


# ── analyze ──


def test_analyze_returns_minimal_signal(stub_marker: type) -> None:
    """analyze() returns a one-page hash-based signal without loading models."""
    backend = factory(_CONFIG)
    result = backend.analyze(_PDF_SOURCE)
    assert result.page_count == 1
    assert len(result.signals) == 1
    sig = result.signals[0]
    assert sig.page_number == 1
    assert sig.has_native_text is True
    assert sig.text_chars == len(b"%PDF-1.4 fake")
    assert sig.blank is False
    assert sig.replacement_char_ratio is None


def test_analyze_empty_content(stub_marker: type) -> None:
    backend = factory(_CONFIG)
    source = SourceDocument(uri="file:///empty.pdf", media_type="application/pdf", content=b"")
    result = backend.analyze(source)
    assert result.signals[0].blank is True
    assert result.signals[0].text_chars == 0


# ── convert ──


def test_convert_pdf_matches_ir(stub_marker: type) -> None:
    """A stubbed PDF converts to the expected page/chunk structure."""
    backend = factory(_CONFIG)
    result = backend.convert(ConversionRequest(source=_PDF_SOURCE))
    assert result.backend.name == "marker"
    assert len(result.pages) == 1
    page = result.pages[0]
    assert isinstance(page, PageResult)
    assert page.page_number == 1
    assert len(page.blocks) == 1
    block = page.blocks[0]
    assert block.kind is ChunkKind.PARAGRAPH
    assert block.content == "Hello world"
    assert block.page_number == 1
    assert block.reading_order == 0
    assert block.metadata == {"backend": "marker"}
    assert result.failures == []


def test_convert_unsupported_format_is_a_typed_record(stub_marker: type) -> None:
    """Non-PDF format is a typed INVALID_INPUT record before any heavy work."""
    backend = factory(_CONFIG)
    result = backend.convert(ConversionRequest(source=_HTML_SOURCE))
    assert result.pages == []
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.code is FailureCode.INVALID_INPUT
    assert "text/html" in failure.detail
    assert "application/pdf" in failure.detail


def test_convert_create_model_dict_fails_returns_dependency_missing(monkeypatch: pytest.MonkeyPatch, reset_converter: None) -> None:
    """create_model_dict failure (network/weights) → DEPENDENCY_MISSING, not a crash."""
    _install_marker_modules(monkeypatch, create_model_dict_raises=OSError("network unreachable"))
    backend = factory(_CONFIG)
    result = backend.convert(ConversionRequest(source=_PDF_SOURCE))
    assert result.pages == []
    assert len(result.failures) == 1
    assert result.failures[0].code is FailureCode.DEPENDENCY_MISSING


def test_convert_conversion_error_returns_backend_error(monkeypatch: pytest.MonkeyPatch, reset_converter: None) -> None:
    """A runtime conversion exception becomes a BACKEND_ERROR failure."""
    _install_marker_modules(monkeypatch, convert_raises=RuntimeError)
    backend = factory(_CONFIG)
    result = backend.convert(ConversionRequest(source=_PDF_SOURCE))
    assert result.pages == []
    assert len(result.failures) == 1
    assert result.failures[0].code is FailureCode.BACKEND_ERROR


def test_convert_cancellation_before_conversion(stub_marker: type, reset_converter: None) -> None:
    """A True cancellation before conversion yields a CANCELLED failure."""
    backend = factory(_CONFIG)
    result = backend.convert(ConversionRequest(source=_PDF_SOURCE, cancellation=lambda: True))
    assert result.pages == []
    assert len(result.failures) == 1
    assert result.failures[0].code is FailureCode.CANCELLED


def test_convert_timeout_before_conversion(stub_marker: type, reset_converter: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """An expired timeout yields a TIMEOUT failure."""
    call_count = 0

    def fake_monotonic() -> float:
        nonlocal call_count
        call_count += 1
        return 0.0 if call_count == 1 else 0.002  # started=0.0, then exceeds deadline 0.001

    impl = importlib.import_module(_IMPL_MODULE)
    monkeypatch.setattr(impl, "monotonic", fake_monotonic)
    backend = factory(_CONFIG)
    result = backend.convert(ConversionRequest(source=_PDF_SOURCE, timeout_s=0.001))
    assert len(result.failures) == 1
    assert result.failures[0].code is FailureCode.TIMEOUT


def test_convert_respects_page_range(stub_marker_paginated: type, reset_converter: None) -> None:
    """page_range filters which pages are returned."""
    backend = factory(_CONFIG)
    result = backend.convert(
        ConversionRequest(
            source=_PDF_SOURCE,
            page_range=PageRange(start=2, end=3),
        )
    )
    assert len(result.pages) == 2
    assert result.pages[0].page_number == 2
    assert result.pages[0].blocks[0].content == "page two"
    assert result.pages[1].page_number == 3
    assert result.pages[1].blocks[0].content == "page three"


def test_convert_respects_output_budget(stub_marker_paginated: type, reset_converter: None) -> None:
    """max_output_chars stops conversion and records a BUDGET_EXCEEDED failure."""
    backend = factory(_CONFIG)
    result = backend.convert(
        ConversionRequest(
            source=_PDF_SOURCE,
            max_output_chars=5,
        )
    )
    # Page 1 ("page one" = 9 chars) exceeds 5
    # Actually, "page one" is 8 chars, and it's > 5
    assert len(result.failures) == 1
    assert result.failures[0].code is FailureCode.BUDGET_EXCEEDED


def test_convert_multi_page_all_pages(stub_marker_paginated: type, reset_converter: None) -> None:
    """All pages present when no range restriction."""
    backend = factory(_CONFIG)
    result = backend.convert(ConversionRequest(source=_PDF_SOURCE))
    assert len(result.pages) == 3
    assert [p.page_number for p in result.pages] == [1, 2, 3]
    assert result.pages[0].blocks[0].content == "page one"
    assert result.pages[1].blocks[0].content == "page two"
    assert result.pages[2].blocks[0].content == "page three"


@pytest.fixture
def stub_marker_paginated(monkeypatch: pytest.MonkeyPatch) -> type:
    """Three-page stub with real pagination fences."""
    markdown = _paginated_markdown(["page one", "page two", "page three"])
    return _install_marker_modules(monkeypatch, markdown=markdown, page_count=3)


def _paginated_markdown(pages: list[str]) -> str:
    r"""Build marker-style paginated markdown (``\n\n{n}---…---\n\n``).

    Uses braces (``{0}``, ``{1}``) matching marker's MarkdownRenderer
    ``pagination_item`` format.
    """
    return "".join(f"\n\n{{{i}}}\n{_PAGE_SEP}\n\n{t}" for i, t in enumerate(pages))


def test_convert_no_markdown_split_returns_single_page(stub_marker: type, reset_converter: None) -> None:
    """When the stubbed markdown has no page markers, one page is produced."""
    backend = factory(_CONFIG)
    result = backend.convert(ConversionRequest(source=_PDF_SOURCE))
    assert len(result.pages) == 1
    assert result.pages[0].page_number == 1


@pytest.fixture
def stub_marker(monkeypatch: pytest.MonkeyPatch) -> type:
    """Default single-page stub."""
    return _install_marker_modules(monkeypatch)


def _install_marker_modules(
    monkeypatch: pytest.MonkeyPatch,
    *,
    markdown: str = "Hello world",
    page_count: int = 1,
    create_model_dict_raises: Exception | type[Exception] | None = None,
    convert_raises: type[Exception] | None = None,
) -> type:
    """Install fake ``marker.*`` modules in ``sys.modules``.

    Returns the ``PdfConverter`` class so tests can tweak instances.
    """
    _create = MagicMock(side_effect=create_model_dict_raises) if create_model_dict_raises is not None else _default_create_dict

    class _MarkdownOutput:
        def __init__(self) -> None:
            self.markdown = markdown
            self.images = {}
            self.metadata = {}

    class _PdfConverter:
        def __init__(self, artifact_dict: object = None) -> None:
            self.artifact_dict = artifact_dict or {}
            self.page_count = page_count

        def __call__(self, filepath: object) -> object:
            self.page_count = page_count
            if convert_raises is not None:
                raise convert_raises("conversion failed")
            return _MarkdownOutput()

    pkg = _stub_module("marker", __path__=[])
    converters_pkg = _stub_module("marker.converters", __path__=[])
    pdf_mod = _stub_module("marker.converters.pdf", PdfConverter=_PdfConverter)
    models_mod = _stub_module("marker.models", create_model_dict=_create)

    def _text_from_rendered(rendered: object) -> tuple[str, str, object]:
        return rendered.markdown, "md", rendered.images  # ty: ignore[unresolved-attribute]

    output_mod = _stub_module("marker.output", text_from_rendered=_text_from_rendered)
    renderers_md = _stub_module("marker.renderers.markdown", MarkdownOutput=_MarkdownOutput, page_separator=_PAGE_SEP)
    settings_mod = _stub_module("marker.settings", paginate_output=True, page_separator=_PAGE_SEP)

    for name, mod in {
        "marker": pkg,
        "marker.converters": converters_pkg,
        "marker.converters.pdf": pdf_mod,
        "marker.models": models_mod,
        "marker.output": output_mod,
        "marker.renderers.markdown": renderers_md,
        "marker.settings": settings_mod,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)

    # Force re-import of the heavy impl so its top-level `from marker...` picks up the stub.
    monkeypatch.delitem(sys.modules, _IMPL_MODULE, raising=False)
    return _PdfConverter


# ── Stub factory ────────────────────────────────────────────────────────────


def _default_create_dict() -> dict[str, bool]:
    """Stand-in for ``marker.models.create_model_dict`` when the stub must succeed."""
    return {"stubs": True}


def _stub_module(name: str, **attributes: object) -> types.ModuleType:
    """A stub module with its attributes attached.

    ``types.ModuleType`` declares no attributes, so assigning them directly is an
    error to ty; ``setattr`` with a constant is B010 to ruff, and ruff --fix rewrites
    it straight back into the assignment ty rejects. Updating ``__dict__`` is the one
    shape both accept.
    """
    module = types.ModuleType(name)
    module.__dict__.update(attributes)
    return module


@pytest.fixture
def reset_converter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the process-wide converter cache between tests."""
    try:
        impl = importlib.import_module(_IMPL_MODULE)
        monkeypatch.setattr(impl, "_CONVERTER", None)
    except ImportError:
        pass  # marker not installed yet — _impl can't be imported


# ── Helper functions (unit tests) ──


class _MarkerImpl(Protocol):
    """The slice of the heavy impl module these tests reach for.

    Declared rather than imported: ``_impl`` carries module-level ``marker``
    imports, so importing it at module scope needs marker-pdf at COLLECTION time
    — which the canonical env does not have (the bring-your-own dependency is
    absent by design). An inline import would be hoisted to the top by pyreorder,
    which is exactly how a function-local import broke ``registry.py``, so the
    accessor goes through ``importlib`` at call time instead.
    """

    def _split_pages(self, markdown: str, page_count: int) -> list[str]:
        """Split marker's paginated markdown into per-page text."""
        ...


def _impl_split_pages(markdown: str, page_count: int) -> list[str]:
    """``_impl._split_pages`` from the stub-imported heavy module.

    Callers must have a stub fixture active (``stub_marker``), which puts the fake
    ``marker.*`` tree in ``sys.modules`` so the heavy impl imports cleanly here.
    """
    module = cast("_MarkerImpl", importlib.import_module(_IMPL_MODULE))
    return module._split_pages(markdown, page_count)


def test_split_pages_single_page_no_markers(stub_marker: type) -> None:
    """Single page when page_count is 1 (no splitting needed)."""
    assert _impl_split_pages("hello world", page_count=1) == ["hello world"]


def test_split_pages_multiple_with_markers(stub_marker: type) -> None:
    """Pages are split at marker fences (braced, 0-based)."""
    md = "\n\n{0}\n" + _PAGE_SEP + "\n\npage1\n\n{1}\n" + _PAGE_SEP + "\n\npage2"
    assert _impl_split_pages(md, page_count=2) == ["page1", "page2"]


def test_split_pages_pads_short(stub_marker: type) -> None:
    """Fewer pages than page_count → padded with empty strings."""
    md = "\n\n{0}\n" + _PAGE_SEP + "\n\npage1"
    assert _impl_split_pages(md, page_count=3) == ["page1", "", ""]


# ── Offline import contract ──


def test_heavy_impl_not_imported_at_package_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing the light factory must NOT load the heavy _impl or marker."""
    for key in list(sys.modules):
        if key.startswith("marker") or key == _IMPL_MODULE:
            monkeypatch.delitem(sys.modules, key, raising=False)
    importlib.reload(parsecraft.backends.marker.marker)
    assert not any(k == "marker" or k.startswith("marker.") for k in sys.modules)
    assert _IMPL_MODULE not in sys.modules
