"""MinerU backend: light/heavy boundary, verified formats, bound-checked convert.

Fully offline: the heavy ``mineru`` / ``pypdfium2`` imports are satisfied by stub
modules in ``sys.modules`` before ``_impl`` loads — the same pattern as the
docling and LiteParse families.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
import re
import subprocess
import sys
import time
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

from parsecraft.backends.errors import BackendError, DependencyUnavailableError
from parsecraft.backends.mineru import mineru as mineru_module
from parsecraft.backends.protocol import BackendConfig, BackendResult, ConversionRequest, DocumentBackend, SourceDocument
from parsecraft.ir.models import ChunkKind, FailureCode, PageRange, PassKind

_PDF_BYTES = b"%PDF-1.7 stub"
_PDF_SOURCE = SourceDocument(uri="file:///doc.pdf", media_type="application/pdf", content=_PDF_BYTES)
_IMPL_MODULE = "parsecraft.backends.mineru._impl"


# ── Spawn guard: docvortex renders through a multiprocessing spawn context ──────

#: The page count is deliberate: docvortex rasterizes every page through
#: ``multiprocessing.get_context("spawn")``, so a multi-page document guarantees
#: the worker pool is actually entered.
_SPAWN_PAGES = 3

#: A console-script-shaped entry point. The ``if __name__ == "__main__"`` guard is
#: the property under test: spawn re-imports the parent's ``__main__`` as
#: ``__mp_main__``, so only a guarded module body stays inert in the children.
_SPAWN_GUARDED = """
import sys
from pathlib import Path

sys.path.insert(0, "__SRC__")

from pypdf import PdfWriter


def _build(path: Path) -> None:
    writer = PdfWriter()
    for _ in range(__PAGES__):
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as handle:
        writer.write(handle)


def main() -> int:
    from parsecraft.backends.protocol import BackendConfig, ConversionRequest, SourceDocument
    from parsecraft.backends.registry import default_registry
    from parsecraft.ir.models import PageRange

    pdf = Path(sys.argv[1])
    _build(pdf)
    backend = default_registry.create("mineru", BackendConfig(name="mineru", options={"image_analysis": True}))
    request = ConversionRequest(
        source=SourceDocument(uri=f"file://{pdf}", media_type="application/pdf", content=None),
        page_range=PageRange(start=1, end=__PAGES__),
        timeout_s=600.0,
    )
    result = backend.convert(request)
    print(f"RESULT pages={len(result.pages)} failures={len(result.failures)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

#: The negative control: the same work with no guard. The spawned children must
#: re-run it, which is what proves the guarded assertion above is not vacuous.
_SPAWN_UNGUARDED = """
import sys
from pathlib import Path

sys.path.insert(0, "__SRC__")

from pypdf import PdfWriter

from parsecraft.backends.protocol import BackendConfig, ConversionRequest, SourceDocument
from parsecraft.backends.registry import default_registry
from parsecraft.ir.models import PageRange

pdf = Path(sys.argv[1])
writer = PdfWriter()
for _ in range(__PAGES__):
    writer.add_blank_page(width=612, height=792)
with pdf.open("wb") as handle:
    writer.write(handle)

backend = default_registry.create("mineru", BackendConfig(name="mineru", options={"image_analysis": True}))
request = ConversionRequest(
    source=SourceDocument(uri=f"file://{pdf}", media_type="application/pdf", content=None),
    page_range=PageRange(start=1, end=__PAGES__),
    timeout_s=600.0,
)
result = backend.convert(request)
print(f"RESULT pages={len(result.pages)} failures={len(result.failures)}")
"""


# ── Stubs: heavy packages faked through sys.modules ─────────────────────────────


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


class _State:
    """Scripted stub state for one test."""

    def __init__(self) -> None:
        self.pdf_pages: list[str] = []
        self.items: list[dict[str, object]] = []
        self.convert_calls: list[dict[str, object]] = []
        self.convert_error: Exception | None = None
        self.delay_s = 0.0


# ── Light factory / descriptor (no heavy import) ────────────────────────────────


def test_descriptor_declares_only_the_verified_pdf_format() -> None:
    descriptor = mineru_module.DESCRIPTOR
    assert descriptor.name == "mineru"
    assert descriptor.version == mineru_module.MINERU_BACKEND_VERSION
    assert descriptor is mineru_module.factory.descriptor
    capabilities = descriptor.capabilities
    assert capabilities.supported_formats == ["application/pdf"]
    assert capabilities.optional_dependency_group == "mineru"
    assert capabilities.supports_page_ranges is True
    assert capabilities.supports_multi_page is True
    # The verified default (text PDF at effort="flash") is weight-free CPU work;
    # the VLM path is a GPU speedup, never a precondition.
    assert capabilities.gpu_requirement == 0.5
    assert capabilities.estimated_vram_gb == 0.0


def test_model_asset_carries_the_corrected_licence_facts() -> None:
    asset = mineru_module.MINERU_ASSET
    assert asset.model_id == "opendatalab/MinerU2.5-Pro-2605-1.2B"
    assert asset.model_revision == "08aaea840498d49ce16247b6263196cf02814885"
    assert asset.model_license == "apache-2.0"
    assert asset.code_license == "LicenseRef-MinerU-Open-Source-License"
    # The Windows-default GGUF engine and the ONNX kit declare no licence.
    assert "undeclared" in asset.asset_license
    assert asset.requires_user_acceptance is False
    # MinerU downloads its own weights; parsecraft pins nothing.
    assert asset.file_pins == ()
    assert asset.size_bytes is None
    assert asset.estimated_vram_gb == 0.0


def test_factory_reports_the_missing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(_name: str) -> object:
        raise ImportError("mineru")

    monkeypatch.setattr(importlib, "import_module", _raise)
    with pytest.raises(
        DependencyUnavailableError,
        match=re.escape("backend dependency 'mineru' is not installed — install the 'mineru' extra"),
    ) as excinfo:
        mineru_module.factory(BackendConfig(name="mineru"))
    assert (excinfo.value.module, excinfo.value.extra) == ("mineru", "mineru")
    assert isinstance(excinfo.value, BackendError)


def test_impl_without_create_entry_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib, "import_module", lambda _name: object())
    with pytest.raises(BackendError, match="must expose create"):
        mineru_module.factory(BackendConfig(name="mineru"))


# ── Heavy impl under the stub ───────────────────────────────────────────────────


def test_factory_returns_the_impl_backend(mineru_stub: _State) -> None:
    # Through the factory, so the impl module's success path is the one under
    # test — ``_backend()`` would bypass ``_load_impl`` entirely.
    backend = mineru_module.factory(BackendConfig(name="mineru"))
    assert backend.name == "mineru"
    assert backend.capabilities is mineru_module.DESCRIPTOR.capabilities


def test_analyze_pdf_maps_page_signals(mineru_stub: _State) -> None:
    mineru_stub.pdf_pages = ["first page text", "   "]
    analysis = _backend().analyze(_PDF_SOURCE)
    assert analysis.page_count == 2
    assert analysis.source_hash == hashlib.sha256(_PDF_BYTES).hexdigest()
    first, second = analysis.signals
    assert (first.page_number, first.has_native_text, first.text_chars, first.blank) == (1, True, 15, False)
    assert (second.page_number, second.has_native_text, second.text_chars, second.blank) == (2, False, 3, True)


def test_analyze_pdf_without_pages_falls_back_to_one_blank_signal(mineru_stub: _State) -> None:
    analysis = _backend().analyze(_PDF_SOURCE)
    assert analysis.page_count == 1
    signal = analysis.signals[0]
    assert (signal.page_number, signal.has_native_text, signal.text_chars, signal.blank) == (1, False, 0, True)


def test_analyze_reads_the_source_file_when_content_is_absent(mineru_stub: _State, tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    path.write_bytes(_PDF_BYTES)
    source = SourceDocument(uri=f"file://{path}", media_type="application/pdf", content=None)
    analysis = _backend().analyze(source)
    assert analysis.source_hash == hashlib.sha256(_PDF_BYTES).hexdigest()


def test_convert_builds_typed_ir_per_page(mineru_stub: _State) -> None:
    mineru_stub.items = [
        {"type": "text", "text": "Title text", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "body paragraph", "page_idx": 0},
        {"type": "list", "list_items": ["one", "two"], "page_idx": 1},
        {"type": "page_number", "text": "1", "page_idx": 1},
    ]
    result = _convert(_backend())
    assert result.failures == []
    assert [page.page_number for page in result.pages] == [1, 2]
    assert [block.kind for block in result.pages[0].blocks] == [ChunkKind.HEADING, ChunkKind.PARAGRAPH]
    assert result.pages[0].blocks[0].id == "mineru-1-b0"
    assert result.pages[0].blocks[0].content == "Title text"
    assert result.pages[1].blocks[0].kind == ChunkKind.LIST
    assert result.pages[1].blocks[0].content == "one\ntwo"


def test_convert_normalizes_the_zero_based_page_index(mineru_stub: _State) -> None:
    """MinerU's content list is 0-based; the IR is 1-based (one place only)."""
    mineru_stub.items = [
        {"type": "text", "text": "page one", "page_idx": 0},
        {"type": "text", "text": "page two", "page_idx": 1},
        {"type": "text", "text": "page three", "page_idx": 2},
    ]
    result = _convert(_backend())
    assert [page.page_number for page in result.pages] == [1, 2, 3]
    assert [block.page_number for block in result.pages[2].blocks] == [3]


def test_convert_maps_table_html_to_plain_text_rows(mineru_stub: _State) -> None:
    mineru_stub.items = [
        {
            "type": "table",
            "table_body": "<table><tr><th>Name</th><th>Value</th></tr><tr><td>a</td><td>1</td></tr></table>",
            "table_caption": ["Table 1: caption"],
            "page_idx": 0,
        }
    ]
    result = _convert(_backend())
    table = result.pages[0].blocks[0]
    assert table.kind == ChunkKind.TABLE
    assert table.content == "Table 1: caption\nName | Value\na | 1"


def test_convert_emits_the_table_caption_when_the_body_is_empty(mineru_stub: _State) -> None:
    mineru_stub.items = [{"type": "table", "table_caption": ["Table 1"], "table_body": "", "page_idx": 0}]
    result = _convert(_backend())
    assert [block.content for block in result.pages[0].blocks] == ["Table 1"]


def test_convert_drops_empty_content(mineru_stub: _State) -> None:
    """The flash path emits equations as images with no LaTeX text -> dropped."""
    mineru_stub.items = [
        {"type": "equation", "text": "", "page_idx": 0},
        {"type": "image", "image_caption": [], "page_idx": 0},
        {"type": "text", "text": "kept", "page_idx": 0},
    ]
    result = _convert(_backend())
    assert [block.kind for block in result.pages[0].blocks] == [ChunkKind.PARAGRAPH]


def test_convert_keeps_equation_text_when_present(mineru_stub: _State) -> None:
    mineru_stub.items = [{"type": "equation", "text": "E = mc^2", "page_idx": 0}]
    result = _convert(_backend())
    assert result.pages[0].blocks[0].kind == ChunkKind.FORMULA
    assert result.pages[0].blocks[0].content == "E = mc^2"


def test_convert_maps_image_captions(mineru_stub: _State) -> None:
    mineru_stub.items = [{"type": "image", "image_caption": ["Figure 1: a chart"], "page_idx": 0}]
    result = _convert(_backend())
    assert result.pages[0].blocks[0].kind == ChunkKind.CAPTION
    assert result.pages[0].blocks[0].content == "Figure 1: a chart"


def test_convert_maps_headers_and_footers(mineru_stub: _State) -> None:
    mineru_stub.items = [
        {"type": "header", "text": "running head", "page_idx": 0},
        {"type": "footer", "text": "page footer", "page_idx": 0},
    ]
    result = _convert(_backend())
    assert [block.kind for block in result.pages[0].blocks] == [ChunkKind.HEADER, ChunkKind.FOOTER]


def test_convert_honours_the_page_range_by_post_filtering(mineru_stub: _State) -> None:
    mineru_stub.items = [
        {"type": "text", "text": "one", "page_idx": 0},
        {"type": "text", "text": "two", "page_idx": 1},
        {"type": "text", "text": "three", "page_idx": 2},
    ]
    result = _convert(_backend(), page_range=PageRange(start=2, end=3))
    assert [page.page_number for page in result.pages] == [2, 3]
    assert [block.content for block in result.pages[0].blocks] == ["two"]
    # The range is never forwarded downstream (its base is unverified).
    assert "page_range" not in mineru_stub.convert_calls[0]


def test_convert_enforces_the_output_budget(mineru_stub: _State) -> None:
    mineru_stub.items = [
        {"type": "text", "text": "aaaa", "page_idx": 0},
        {"type": "text", "text": "bbbb", "page_idx": 1},
    ]
    result = _convert(_backend(), max_output_chars=5)
    assert [page.page_number for page in result.pages] == [1]
    failure = result.failures[0]
    assert failure.code == FailureCode.BUDGET_EXCEEDED
    assert failure.pass_kind == PassKind.NATIVE
    assert failure.backend == "mineru"
    assert failure.backend_version == mineru_module.MINERU_BACKEND_VERSION
    assert failure.page_range is None


def test_convert_enforces_the_timeout_deadline(mineru_stub: _State) -> None:
    mineru_stub.delay_s = 0.05
    result = _convert(_backend(), timeout_s=0.01)
    assert result.pages == []
    assert result.failures[0].code == FailureCode.TIMEOUT
    assert result.failures[0].budget_s == 0.01


def test_convert_reports_cancellation(mineru_stub: _State) -> None:
    mineru_stub.items = [{"type": "text", "text": "one", "page_idx": 0}]
    result = _convert(_backend(), cancel=True)
    assert result.failures[0].code == FailureCode.CANCELLED
    assert mineru_stub.convert_calls == []


def test_convert_reports_cancellation_between_items(mineru_stub: _State) -> None:
    mineru_stub.items = [
        {"type": "text", "text": "one", "page_idx": 0},
        {"type": "text", "text": "two", "page_idx": 0},
    ]
    # ``convert`` checks cancellation once before the loop, so the iterator
    # yields the pre-check, item one, then the between-items cancellation.
    remaining = iter([False, False, True])
    request = ConversionRequest(source=_PDF_SOURCE, cancellation=lambda: next(remaining))
    result = _backend().convert(request)
    assert result.failures[0].code == FailureCode.CANCELLED
    # The first item was already emitted when the cancellation took effect.
    assert [block.content for block in result.pages[0].blocks] == ["one"]


def test_convert_reports_backend_errors_as_typed_failures(mineru_stub: _State) -> None:
    mineru_stub.convert_error = RuntimeError("boom")
    result = _convert(_backend())
    assert result.pages == []
    failure = result.failures[0]
    assert failure.code == FailureCode.BACKEND_ERROR
    assert "RuntimeError: boom" in failure.detail


def test_source_bytes_reads_content_and_wraps_read_errors(mineru_stub: _State, tmp_path: Path) -> None:
    impl = _impl()
    assert impl.source_bytes(_PDF_SOURCE) == _PDF_BYTES
    missing = tmp_path / "missing.pdf"
    with pytest.raises(BackendError, match="cannot read source"):
        impl.source_bytes(SourceDocument(uri=missing.absolute().as_uri()))


def test_convert_reports_the_verified_default_effort(mineru_stub: _State) -> None:
    _convert(_backend())
    call = mineru_stub.convert_calls[0]
    assert call["effort"] == "flash"
    assert call["parse_mode"] == "auto"
    assert call["image_analysis"] is False
    assert call["file_suffix"] == "pdf"


def test_convert_accepts_the_configured_effort(mineru_stub: _State) -> None:
    _convert(_backend(BackendConfig(name="mineru", options={"effort": "high"})))
    assert mineru_stub.convert_calls[0]["effort"] == "high"


def test_convert_rejects_an_unknown_effort(mineru_stub: _State) -> None:
    with pytest.raises(BackendError, match="unknown effort"):
        _backend(BackendConfig(name="mineru", options={"effort": "turbo"}))


def test_convert_rejects_a_non_boolean_image_analysis(mineru_stub: _State) -> None:
    with pytest.raises(BackendError, match="image_analysis"):
        _backend(BackendConfig(name="mineru", options={"image_analysis": "yes"}))


def test_convert_enables_image_analysis_on_request(mineru_stub: _State) -> None:
    _convert(_backend(BackendConfig(name="mineru", options={"image_analysis": True})))
    assert mineru_stub.convert_calls[0]["image_analysis"] is True


def test_create_points_mineru_home_into_the_managed_cache(mineru_stub: _State, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ADR-0007 d4: the self-fetched weights must sit where the cache tooling looks."""
    monkeypatch.setattr("parsecraft.assets.manager.user_cache_path", lambda _appname: tmp_path / "cache")
    _backend()
    asset = mineru_module.MINERU_ASSET
    expected = tmp_path / "cache" / "models" / asset.model_id.replace("/", "--") / asset.model_revision
    # <cache root>/models/<slug>/<revision> — a revision dir of the managed
    # layout, never a sibling of it: `models list`/`clean`/`remove` must see it.
    assert Path(os.environ["MINERU_HOME"]) == expected


def test_create_respects_an_existing_mineru_home(mineru_stub: _State, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MINERU_HOME", str(tmp_path / "user-cache"))
    _backend()
    assert os.environ["MINERU_HOME"] == str(tmp_path / "user-cache")


def test_convert_reports_the_model_reference(mineru_stub: _State) -> None:
    result = _convert(_backend())
    reference = result.backend
    assert reference.name == "mineru"
    assert reference.version == mineru_module.MINERU_BACKEND_VERSION
    assert reference.model_id == mineru_module.MINERU_ASSET.model_id
    assert reference.model_revision == mineru_module.MINERU_ASSET.model_revision


def _backend(config: BackendConfig | None = None) -> DocumentBackend:
    return _impl().create(config or BackendConfig(name="mineru"))


def _convert(
    backend: DocumentBackend,
    *,
    page_range: PageRange | None = None,
    max_output_chars: int | None = None,
    timeout_s: float | None = None,
    cancel: bool = False,
) -> BackendResult:
    request = ConversionRequest(
        source=_PDF_SOURCE,
        page_range=page_range,
        max_output_chars=max_output_chars,
        timeout_s=timeout_s,
        cancellation=(lambda: True) if cancel else None,
    )
    return backend.convert(request)


def test_table_text_extractor_handles_nested_markup(mineru_stub: _State) -> None:
    extractor = _impl()._TableTextExtractor()
    extractor.feed("<table><tr><td><b>bold</b> cell</td><td>second</td></tr></table>")
    extractor.close()
    assert extractor.render() == "bold cell | second"


def test_table_text_extractor_ignores_empty_cells(mineru_stub: _State) -> None:
    extractor = _impl()._TableTextExtractor()
    extractor.feed("<table><tr><td>a</td><td>   </td></tr></table>")
    extractor.close()
    assert extractor.render() == "a"


def test_joined_treats_a_non_list_field_as_empty(mineru_stub: _State) -> None:
    assert _impl()._joined("not a list") == ""
    assert _impl()._joined(None) == ""


def test_map_item_returns_none_for_page_furniture(mineru_stub: _State) -> None:
    assert _impl()._map_item({"type": "page_number", "text": "1"}) == (None, "")


def test_map_item_returns_none_for_unknown_types(mineru_stub: _State) -> None:
    assert _impl()._map_item({"type": "watermark", "text": "x"}) == (None, "")


def test_page_number_is_the_single_normalization_point(mineru_stub: _State) -> None:
    assert _impl()._page_number({"page_idx": 0}) == 1
    assert _impl()._page_number({"page_idx": 79}) == 80


@pytest.fixture
def mineru_stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[_State]:
    """Install the stub package tree and force a fresh heavy-impl import."""
    state = _State()
    saved_env = dict(os.environ)

    def _doc_analyze(data: bytes, **kwargs: object) -> tuple[dict[str, object], object]:
        state.convert_calls.append({"data": data, **kwargs})
        if state.convert_error is not None:
            raise state.convert_error
        if state.delay_s > 0:
            time.sleep(state.delay_s)
        return {"pages": [{"page_idx": 0}]}, object()

    def _render_content_list(_middle_json: dict[str, object]) -> list[dict[str, object]]:
        return list(state.items)

    class _PdfDocument:
        def __init__(self, _data: bytes) -> None:
            self._pages = [_PdfPage(text) for text in state.pdf_pages]
            self.closed = False

        def __len__(self) -> int:
            return len(self._pages)

        def __getitem__(self, index: int) -> _PdfPage:
            return self._pages[index]

        def close(self) -> None:
            self.closed = True

    mineru_pkg = types.ModuleType("mineru")
    analyze_pkg = types.ModuleType("mineru.backend")
    analyze_mod = types.ModuleType("mineru.backend.analyze")
    render_mod = types.ModuleType("mineru.render")
    pdfium_mod = types.ModuleType("pypdfium2")
    # ``__dict__.update`` is the spelling ruff (B010) and ty both accept:
    # ``setattr(module, ...)`` is rewritten to attribute assignment by --fix,
    # which ty then rejects on a ModuleType.
    analyze_mod.__dict__.update({"doc_analyze": _doc_analyze})
    render_mod.__dict__.update({"render_content_list": _render_content_list})
    pdfium_mod.__dict__.update({"PdfDocument": _PdfDocument})
    monkeypatch.setitem(sys.modules, "mineru", mineru_pkg)
    monkeypatch.setitem(sys.modules, "mineru.backend", analyze_pkg)
    monkeypatch.setitem(sys.modules, "mineru.backend.analyze", analyze_mod)
    monkeypatch.setitem(sys.modules, "mineru.render", render_mod)
    monkeypatch.setitem(sys.modules, "pypdfium2", pdfium_mod)
    monkeypatch.delenv("MINERU_HOME", raising=False)
    monkeypatch.delitem(sys.modules, _IMPL_MODULE, raising=False)
    try:
        yield state
    finally:
        sys.modules.pop(_IMPL_MODULE, None)
        os.environ.clear()
        os.environ.update(saved_env)


def _impl() -> types.ModuleType:
    return importlib.import_module(_IMPL_MODULE)


@pytest.mark.skipif(
    not importlib.util.find_spec("mineru") or not importlib.util.find_spec("pypdf"),
    reason="mineru extra (and pypdf to build the document) not installed",
)
def test_console_script_spawn_guard_survives_docvortex(tmp_path: Path) -> None:
    """A console-script entry point must not re-run the conversion in spawn children.

    docvortex rasterizes page images through ``multiprocessing.get_context("spawn")``,
    and spawn re-imports the parent's ``__main__`` as ``__mp_main__``. An unguarded
    module body therefore re-executes the whole workload inside every render worker;
    the ``if __name__ == "__main__"`` guard is what keeps the backend's own module
    body inert. Verified here end to end: the guarded worker prints ``RESULT`` once,
    the unguarded control prints it more than once.
    """
    guarded = _run_spawn_worker(tmp_path, _SPAWN_GUARDED, "guarded.py")
    assert len(guarded) == 1, f"the guard did not hold: {guarded}"
    unguarded = _run_spawn_worker(tmp_path, _SPAWN_UNGUARDED, "unguarded.py")
    assert len(unguarded) > 1, (
        "the negative control ran only once, so this environment no longer re-imports "
        "__main__ in spawn children — the hazard this test pins has changed upstream "
        f"(revisit the guard requirement): {unguarded}"
    )


def _run_spawn_worker(tmp_path: Path, script: str, name: str) -> list[str]:
    """Run a worker script as a real subprocess; return its ``RESULT`` lines."""
    source = script.replace("__SRC__", str(Path(__file__).resolve().parents[1] / "src"))
    source = source.replace("__PAGES__", str(_SPAWN_PAGES))
    script_path = tmp_path / name
    script_path.write_text(source, encoding="utf-8")
    env = {**os.environ, "MINERU_HOME": str(tmp_path / "mineru-home")}
    completed = subprocess.run(  # noqa: S603 — fixed interpreter, repo-local worker script
        [sys.executable, str(script_path), str(tmp_path / f"{name}.pdf")],
        capture_output=True,
        text=True,
        timeout=900,
        env=env,
        check=False,
    )
    return [line for line in completed.stdout.splitlines() if line.startswith("RESULT")]
