"""OCR/VLM backend adapters: descriptors, light/heavy boundary, bounds, failures.

Fully offline: the heavy ``transformers``/``vllm``/``PIL``/``pymupdf`` surfaces
are stubbed through ``sys.modules`` before the impl modules are imported, and
nothing here may touch the network or download a model.
"""

from __future__ import annotations

import hashlib
import importlib
import re
import sys
import time
from collections.abc import Callable, Iterator
from io import BytesIO
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import pytest

from parsecraft.backends import registry as registry_module
from parsecraft.backends.errors import BackendError
from parsecraft.backends.ocr import _common, _models, ovis, qianfan, tele, unlimited
from parsecraft.backends.ocr._common import VllmCompletion, VllmOutput
from parsecraft.backends.protocol import (
    BackendConfig,
    BackendDescriptor,
    BackendFactory,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
    ModelAssetDescriptor,
    SourceDocument,
)
from parsecraft.ir.models import FailureCode, PageRange, PassKind

_PNG = b"\x89PNG\r\n\x1a\nfake-png-payload"
_PDF_MAGIC = b"%PDF-1.4\n"
_PDF_SOURCE = SourceDocument(uri="file:///doc.pdf", content=_PDF_MAGIC)


_FACTORY_MODULES: tuple[tuple[FactoryModule, str, str, float, bool], ...] = (
    (ovis, "ocr-ovis", "ocr-ovis", 1.0, False),
    (tele, "ocr-tele", "ocr-tele", 1.2, False),
    (unlimited, "ocr-unlimited", "ocr-unlimited", 3.0, True),
    (qianfan, "ocr-qianfan", "ocr-qianfan", 4.0, False),
)

_ASSETS: tuple[tuple[FactoryModule, ModelAssetDescriptor, str, str], ...] = (
    (ovis, _models.OVIS_ASSET, "ATH-MaaS/OvisOCR2", "apache-2.0"),
    (tele, _models.TELE_ASSET, "StarDoc-AI/TeleOCR", "apache-2.0"),
    (unlimited, _models.UNLIMITED_ASSET, "baidu/Unlimited-OCR", "MIT"),
    (qianfan, _models.QIANFAN_ASSET, "baidu/Qianfan-OCR", "apache-2.0"),
)


class FactoryModule(Protocol):
    """Shape of a light OCR entry-point module: descriptor + factory, nothing heavy."""

    @property
    def DESCRIPTOR(self) -> BackendDescriptor:
        """The module-level descriptor constant."""
        ...

    @property
    def factory(self) -> BackendFactory:
        """The entry-point factory object."""
        ...


# ── Stubs: heavy surfaces faked through sys.modules ─────────────────────────────


class _PixmapStub:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def tobytes(self, output_format: str) -> bytes:
        assert output_format == "png"
        return self._payload


class _PdfPageStub:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def get_pixmap(self) -> _PixmapStub:
        return _PixmapStub(self._payload)


class _PdfDocumentStub:
    def __init__(self, page_count: int, raster: bytes) -> None:
        self.page_count = page_count
        self._raster = raster
        self.loaded: list[int] = []
        self.closed = False

    def load_page(self, page_number: int) -> _PdfPageStub:
        self.loaded.append(page_number)
        return _PdfPageStub(self._raster)

    def close(self) -> None:
        self.closed = True


class _PymupdfStub:
    """Offline stand-in for PyMuPDF (extra ``pdf``)."""

    def __init__(self) -> None:
        self.page_count = 3
        self.raster = b"\x89PNG\r\n\x1a\nraster-png"
        self.open_calls: list[tuple[bytes, str]] = []
        self.documents: list[_PdfDocumentStub] = []

    def open(self, *, stream: bytes, filetype: str) -> _PdfDocumentStub:
        self.open_calls.append((stream, filetype))
        document = _PdfDocumentStub(self.page_count, self.raster)
        self.documents.append(document)
        return document


class _ImageStub:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload


class _PilImageStub:
    """Offline stand-in for ``PIL.Image`` (pillow ships with the OCR extras)."""

    @staticmethod
    def open(stream: BytesIO) -> _ImageStub:
        return _ImageStub(stream.read())


class _SamplingParamsStub:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _CompletionStub:
    def __init__(self, text: str) -> None:
        self.text = text


class _VllmOutputStub:
    def __init__(self, text: str) -> None:
        self.outputs: list[VllmCompletion] = [_CompletionStub(text)]


class _EngineStub:
    def __init__(self) -> None:
        self.calls: list[tuple[list[dict[str, object]], _SamplingParamsStub]] = []
        self.tokenizer = _ChatTemplateStub()
        self.text = "vllm stub page text"

    def get_tokenizer(self) -> _ChatTemplateStub:
        return self.tokenizer

    def generate(
        self,
        requests: list[dict[str, object]],
        sampling: object,
    ) -> list[VllmOutput]:
        assert isinstance(sampling, _SamplingParamsStub)
        self.calls.append((requests, sampling))
        return cast("list[VllmOutput]", [_VllmOutputStub(self.text)])


class _VllmStub:
    """Offline stand-in for the optional vLLM runtime (extra ``vllm``)."""

    SamplingParams = _SamplingParamsStub

    def __init__(self) -> None:
        self.engine = _EngineStub()
        self.llm_calls: list[dict[str, object]] = []

    def LLM(self, **kwargs: object) -> _EngineStub:  # noqa: N802 — mirrors the vLLM API
        self.llm_calls.append(kwargs)
        return self.engine


class _FakePipeline:
    def __init__(self, state: _TransformersStub) -> None:
        self._state = state
        self.tokenizer = state.chat_template
        self.calls: list[dict[str, object]] = []

    def __call__(self, *, text: str, images: object, **generation: object) -> object:
        call: dict[str, object] = {"text": text, "images": images}
        call.update(generation)
        self.calls.append(call)
        if self._state.call_error is not None:
            raise self._state.call_error
        outputs = self._state.pipeline_outputs
        return outputs.pop(0) if len(outputs) > 1 else outputs[0]


class _FakeLongHorizonModel:
    """Offline stand-in for Unlimited-OCR's card ``infer_multi()`` surface."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, str, int]] = []
        self.settings: list[tuple[int, int, int]] = []
        self.output_dir_existed: list[bool] = []
        self.raw_outputs: list[object] = []
        self.error: Exception | None = None

    def eval(self) -> _FakeLongHorizonModel:
        """Mirrors ``torch.nn.Module.eval()`` (returns self)."""
        return self

    def infer_multi(
        self,
        tokenizer: object,
        *,
        prompt: str,
        image_files: list[str],
        output_path: str,
        image_size: int,
        max_length: int,
        no_repeat_ngram_size: int,
        ngram_window: int,
    ) -> tuple[object, int]:
        self.calls.append((len(image_files), prompt, max_length))
        self.settings.append((image_size, no_repeat_ngram_size, ngram_window))
        self.output_dir_existed.append(Path(output_path).is_dir())
        if self.error is not None:
            raise self.error
        if self.raw_outputs:
            raw = self.raw_outputs.pop(0) if len(self.raw_outputs) > 1 else self.raw_outputs[0]
        else:
            raw = "<PAGE>" + "\n<PAGE>".join(f"stub multi {index}" for index in range(len(image_files)))
        return raw, 0


class _AutoModelStub:
    def __init__(self, state: _TransformersStub) -> None:
        self._state = state

    def from_pretrained(self, model_id: str, **kwargs: object) -> _FakeLongHorizonModel:
        self._state.load_calls.append({"model_id": model_id, **kwargs})
        if self._state.load_error is not None:
            raise self._state.load_error
        return self._state.model


class _AutoTokenizerStub:
    def __init__(self, state: _TransformersStub) -> None:
        self._state = state

    def from_pretrained(self, model_id: str, **kwargs: object) -> _ChatTemplateStub:
        self._state.tokenizer_load_calls.append({"model_id": model_id, **kwargs})
        if self._state.tokenizer_error is not None:
            raise self._state.tokenizer_error
        return self._state.chat_template


class _ChatTemplateStub:
    """Offline stand-in for ``tokenizer.apply_chat_template(...)``."""

    def __init__(self, *, accepts_thinking: bool = True) -> None:
        self.accepts_thinking = accepts_thinking
        self.calls: list[tuple[list[dict[str, object]], dict[str, object]]] = []

    def apply_chat_template(self, conversation: list[dict[str, object]], **options: object) -> str:
        self.calls.append((list(conversation), dict(options)))  # record every attempt, incl. rejected ones
        if "enable_thinking" in options and not self.accepts_thinking:
            msg = "apply_chat_template() got an unexpected keyword argument 'enable_thinking'"
            raise TypeError(msg)
        return "TEMPLATED_PROMPT"


class _TransformersStub:
    """Offline stand-in for the ``transformers`` package (never the real thing)."""

    def __init__(self) -> None:
        self.pipeline_calls: list[dict[str, object]] = []
        self.pipeline_outputs: list[object] = [[{"generated_text": "stub page text"}]]
        self.pipeline_error: Exception | None = None
        self.call_error: Exception | None = None
        self.load_calls: list[dict[str, object]] = []
        self.load_error: Exception | None = None
        self.last_pipeline: _FakePipeline | None = None
        self.model = _FakeLongHorizonModel()
        self.AutoModel = _AutoModelStub(self)
        self.AutoTokenizer = _AutoTokenizerStub(self)
        self.tokenizer_load_calls: list[dict[str, object]] = []
        self.tokenizer_error: Exception | None = None
        self.chat_template = _ChatTemplateStub()

    def pipeline(self, **kwargs: object) -> _FakePipeline:
        self.pipeline_calls.append(dict(kwargs))
        if self.pipeline_error is not None:
            raise self.pipeline_error
        self.last_pipeline = _FakePipeline(self)
        return self.last_pipeline


class _SentinelBackend:
    """What a fake impl's create() returns — identity-checked only."""

    name = "sentinel"


# ── Descriptors and model assets (light, no stubs) ──────────────────────────────


def test_factory_modules_satisfy_the_entry_point_protocol() -> None:
    modules: list[FactoryModule] = [ovis, tele, unlimited, qianfan]
    assert [module.factory.descriptor.name for module in modules] == [
        "ocr-ovis",
        "ocr-tele",
        "ocr-unlimited",
        "ocr-qianfan",
    ]


@pytest.mark.parametrize(("module", "name", "extra", "vram", "multi_page"), _FACTORY_MODULES)
def test_factory_descriptor_matches_the_plan_table(
    module: FactoryModule,
    *,
    name: str,
    extra: str,
    vram: float,
    multi_page: bool,
) -> None:
    descriptor = module.factory.descriptor
    assert isinstance(descriptor, BackendDescriptor)
    assert descriptor.name == name
    assert descriptor.version == _models.OCR_BACKEND_VERSION
    capabilities = descriptor.capabilities
    assert capabilities.requires_gpu is True
    assert capabilities.estimated_vram_gb == vram
    assert capabilities.optional_dependency_group == extra
    assert capabilities.supports_page_ranges is True
    assert capabilities.supports_multi_page is multi_page
    assert capabilities.supported_formats == ["application/pdf", "image/jpeg", "image/png"]
    assert module.DESCRIPTOR is descriptor


@pytest.mark.parametrize(("module", "asset", "model_id", "license_name"), _ASSETS)
def test_model_assets_are_pinned_and_permissive(
    module: FactoryModule,
    asset: ModelAssetDescriptor,
    *,
    model_id: str,
    license_name: str,
) -> None:
    assert asset.model_id == model_id
    assert re.fullmatch(r"[0-9a-f]{40}", asset.model_revision)
    assert asset.model_license == license_name
    assert asset.asset_license == license_name
    assert asset.code_license == "MIT"
    assert asset.requires_user_acceptance is False
    assert asset.size_bytes is None
    assert asset.quantization is None
    assert asset.source_urls
    assert all(url.startswith("https://") for url in asset.source_urls)
    capabilities = module.factory.descriptor.capabilities
    assert asset.estimated_vram_gb == capabilities.estimated_vram_gb


@pytest.mark.parametrize(("module", "name", "extra", "_vram", "_multi"), _FACTORY_MODULES)
def test_entry_point_module_is_registry_ready(
    module: FactoryModule,
    *,
    name: str,
    extra: str,
    _vram: float,
    _multi: bool,
) -> None:
    """The pyproject entry point ``<name> = module:factory`` must register cleanly."""
    registry = registry_module.BackendRegistry()
    registry._entry_points_loaded = True  # isolate from installed entry points
    registry.register(name, module.factory)
    assert registry.get(name).name == name
    assert [d.name for d in registry.list_backends()] == [name]


def test_entry_point_group_is_the_frozen_public_name() -> None:
    assert registry_module.ENTRY_POINT_GROUP == "parsecraft.backends"


# ── Light factory boundary ──────────────────────────────────────────────────────


@pytest.mark.parametrize(("module", "name", "extra", "_vram", "_multi"), _FACTORY_MODULES)
def test_missing_extra_raises_typed_error_with_install_hint(
    module: FactoryModule,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str,
    extra: str,
    _vram: float,
    _multi: bool,
) -> None:
    real_import = importlib.import_module

    def _fake_import(module_name: str, *_args: object, **_kwargs: object) -> ModuleType:
        if module_name.startswith("parsecraft.backends.ocr._"):
            msg = f"No module named {module_name!r}"
            raise ModuleNotFoundError(msg, name="transformers")
        return real_import(module_name)

    monkeypatch.setattr(importlib, "import_module", _fake_import)
    with pytest.raises(BackendError, match=re.escape(f"pip install 'parsecraft[{extra}]'")) as excinfo:
        module.factory(BackendConfig(name=name))
    assert "transformers" in str(excinfo.value)


@pytest.mark.parametrize(("module", "name", "extra", "_vram", "_multi"), _FACTORY_MODULES)
def test_impl_without_create_entry_is_rejected(
    module: FactoryModule,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str,
    extra: str,
    _vram: float,
    _multi: bool,
) -> None:
    monkeypatch.setattr(importlib, "import_module", lambda _name: object())
    with pytest.raises(BackendError, match="must expose create"):
        module.factory(BackendConfig(name=name))


@pytest.mark.parametrize(("module", "name", "extra", "_vram", "_multi"), _FACTORY_MODULES)
def test_factory_returns_the_injected_impl_backend(
    module: FactoryModule,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str,
    extra: str,
    _vram: float,
    _multi: bool,
) -> None:
    sentinel = _SentinelBackend()

    class _StubImpl:
        @staticmethod
        def create(config: BackendConfig) -> DocumentBackend:
            del config
            return cast("DocumentBackend", sentinel)

    monkeypatch.setattr(importlib, "import_module", lambda _name: _StubImpl())
    result = module.factory(BackendConfig(name=name))
    assert result is sentinel


def test_source_bytes_prefers_content_and_reads_files(tmp_path: Path) -> None:
    assert _common.source_bytes(_image_source(b"inline")) == b"inline"
    file_path = tmp_path / "doc.png"
    file_path.write_bytes(_PNG)
    assert _common.source_bytes(SourceDocument(uri=f"file://{file_path}")) == _PNG


def test_source_bytes_rejects_remote_uri_without_content() -> None:
    with pytest.raises(BackendError, match="must carry in-memory content"):
        _common.source_bytes(SourceDocument(uri="https://example.com/doc.pdf"))


def test_source_bytes_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(BackendError, match="cannot read source"):
        _common.source_bytes(SourceDocument(uri=f"file://{tmp_path / 'gone.png'}"))


def test_count_pages_is_one_for_images_and_uses_the_pdf_engine(pdf_engine: _PymupdfStub) -> None:
    assert _common.count_pages(_image_source()) == 1
    pdf_engine.page_count = 5
    assert _common.count_pages(_PDF_SOURCE) == 5
    assert pdf_engine.documents[0].closed is True
    assert pdf_engine.open_calls[0][1] == "pdf"


def test_count_pages_rejects_unsupported_payload() -> None:
    with pytest.raises(BackendError, match="expected a PDF or a PNG/JPEG image"):
        _common.count_pages(SourceDocument(uri="file:///doc.txt", content=b"plain text"))


def test_declared_formats_match_the_page_access_layer() -> None:
    """Single MIME vocabulary: declared == what count_pages/rasterize accept."""
    assert set(_models.OCR_FORMATS) == {"application/pdf", "image/jpeg", "image/png"}
    jpeg = SourceDocument(uri="file:///scan.jpg", content=b"\xff\xd8\xff\xe0jpeg-payload")
    assert _common.count_pages(jpeg) == 1  # JPEG magic, no PDF engine needed
    assert _common.rasterize_page(jpeg, 1) == jpeg.content


def test_rasterize_page_passes_images_through_and_maps_pdf_indices(pdf_engine: _PymupdfStub) -> None:
    assert _common.rasterize_page(_image_source(), 1) == _PNG
    raster = _common.rasterize_page(_PDF_SOURCE, 3)
    assert raster == pdf_engine.raster
    assert pdf_engine.documents[0].loaded == [2]  # page 3 → zero-based index 2
    assert pdf_engine.documents[0].closed is True


def test_analyze_is_deterministic_for_images() -> None:
    first = _common.analyze_source(_image_source())
    second = _common.analyze_source(_image_source())
    assert first == second
    assert first.source_hash == hashlib.sha256(_PNG).hexdigest()
    assert first.page_count == 1
    signal = first.signals[0]
    assert signal.has_native_text is False
    assert signal.text_chars == 0
    assert signal.image_count == 1
    assert signal.blank is False


# ── _common: sources, pages, analysis ───────────────────────────────────────────


def _image_source(payload: bytes = _PNG) -> SourceDocument:
    return SourceDocument(uri="file:///page.png", content=payload)


def test_analyze_covers_every_pdf_page(pdf_engine: _PymupdfStub) -> None:
    pdf_engine.page_count = 4
    analysis = _common.analyze_source(_PDF_SOURCE)
    assert analysis.page_count == 4
    assert [signal.page_number for signal in analysis.signals] == [1, 2, 3, 4]


def test_analyze_reads_pdf_from_disk(tmp_path: Path, pdf_engine: _PymupdfStub) -> None:
    pdf_engine.page_count = 2
    file_path = tmp_path / "doc.pdf"
    file_path.write_bytes(_PDF_MAGIC)
    analysis = _common.analyze_source(SourceDocument(uri=f"file://{file_path}"))
    assert analysis.page_count == 2


# ── _common: conversion bounds ──────────────────────────────────────────────────


def test_convert_covers_all_pages_by_default(pdf_engine: _PymupdfStub) -> None:
    pdf_engine.page_count = 3
    seen: list[int] = []
    result = _convert(
        _PDF_SOURCE,
        _request(_PDF_SOURCE),
        lambda number, _req: seen.append(number) or f"text-{number}",
    )
    assert seen == [1, 2, 3]
    assert [page.page_number for page in result.pages] == [1, 2, 3]
    assert result.pages[0].blocks[0].content == "text-1"
    assert result.pages[0].blocks[0].id == "ocr-test-p1-b0"
    assert result.failures == []
    assert result.backend.model_id == _models.OVIS_MODEL_ID
    assert result.backend.model_revision == _models.OVIS_REVISION
    assert result.backend.version == "9.9.9"
    assert result.elapsed_s >= 0.0


def test_convert_honors_page_range(pdf_engine: _PymupdfStub) -> None:
    pdf_engine.page_count = 5
    seen: list[int] = []
    result = _convert(
        _PDF_SOURCE,
        _request(_PDF_SOURCE, page_range=(2, 3)),
        lambda number, _req: seen.append(number) or "x",
    )
    assert seen == [2, 3]
    assert [page.page_number for page in result.pages] == [2, 3]
    assert result.failures == []


def test_convert_clamps_a_partially_overlapping_range(pdf_engine: _PymupdfStub) -> None:
    pdf_engine.page_count = 4
    seen: list[int] = []
    result = _convert(
        _PDF_SOURCE,
        _request(_PDF_SOURCE, page_range=(3, 10)),
        lambda number, _req: seen.append(number) or "x",
    )
    assert seen == [3, 4]
    assert [page.page_number for page in result.pages] == [3, 4]


def test_convert_rejects_a_range_outside_the_document(pdf_engine: _PymupdfStub) -> None:
    pdf_engine.page_count = 2
    result = _convert(
        _PDF_SOURCE,
        _request(_PDF_SOURCE, page_range=(7, 9)),
        lambda _number, _req: "x",
    )
    assert result.pages == []
    failure = result.failures[0]
    assert failure.code is FailureCode.INVALID_INPUT
    assert failure.page_range is not None
    assert failure.page_range.start == 7
    assert "outside the document" in failure.detail


def test_convert_reports_empty_documents_as_invalid(pdf_engine: _PymupdfStub) -> None:
    pdf_engine.page_count = 0
    result = _convert(_PDF_SOURCE, _request(_PDF_SOURCE), lambda _number, _req: "x")
    assert result.pages == []
    assert result.failures[0].code is FailureCode.INVALID_INPUT
    assert "no pages" in result.failures[0].detail


def test_convert_turns_unsupported_sources_into_typed_failures() -> None:
    source = SourceDocument(uri="file:///doc.txt", content=b"plain text")
    result = _convert(source, _request(source), lambda _number, _req: "x")
    assert result.pages == []
    assert result.failures[0].code is FailureCode.INVALID_INPUT
    assert "expected a PDF or a PNG/JPEG image" in result.failures[0].detail


def test_convert_stops_on_cancellation(pdf_engine: _PymupdfStub) -> None:
    pdf_engine.page_count = 5
    calls = {"n": 0}

    def _infer(number: int, _req: ConversionRequest) -> str:
        calls["n"] += 1
        return f"text-{number}"

    result = _convert(
        _PDF_SOURCE,
        _request(_PDF_SOURCE, cancellation=lambda: calls["n"] >= 2),
        _infer,
    )
    assert [page.page_number for page in result.pages] == [1, 2]
    failure = result.failures[0]
    assert failure.code is FailureCode.CANCELLED
    assert failure.pass_kind is PassKind.VISUAL
    assert failure.backend == "ocr-test"


def test_convert_times_out_on_an_exhausted_budget(pdf_engine: _PymupdfStub) -> None:
    pdf_engine.page_count = 3

    def _slow_infer(_number: int, _req: ConversionRequest) -> str:
        time.sleep(0.05)  # overshoot the 10 ms budget so page 2 sees a spent deadline
        return "x"

    result = _convert(
        _PDF_SOURCE,
        _request(_PDF_SOURCE, timeout_s=0.01),
        _slow_infer,
    )
    assert [page.page_number for page in result.pages] == [1]
    failure = result.failures[0]
    assert failure.code is FailureCode.TIMEOUT
    assert failure.budget_s == 0.01
    assert failure.detail.startswith("time budget")


def test_convert_enforces_the_output_budget(pdf_engine: _PymupdfStub) -> None:
    pdf_engine.page_count = 5
    result = _convert(
        _PDF_SOURCE,
        _request(_PDF_SOURCE, max_output_chars=15),
        lambda _number, _req: "0123456789",
    )
    assert [page.page_number for page in result.pages] == [1]
    failure = result.failures[0]
    assert failure.code is FailureCode.BUDGET_EXCEEDED
    assert "15 chars" in failure.detail


def test_convert_records_per_page_errors_and_continues(pdf_engine: _PymupdfStub) -> None:
    pdf_engine.page_count = 3

    def _infer(number: int, _req: ConversionRequest) -> str:
        if number == 2:
            msg = "cuda OOM"
            raise RuntimeError(msg)
        return f"text-{number}"

    result = _convert(_PDF_SOURCE, _request(_PDF_SOURCE), _infer)
    assert [page.page_number for page in result.pages] == [1, 3]
    failure = result.failures[0]
    assert failure.code is FailureCode.BACKEND_ERROR
    assert "page 2: RuntimeError: cuda OOM" in failure.detail
    assert failure.page_range is not None
    assert (failure.page_range.start, failure.page_range.end) == (2, 2)
    assert failure.occurred_at.tzinfo is not None


def test_missing_pdf_engine_names_the_pdf_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """PyMuPDF stays an explicit AGPL opt-in: the error must name parsecraft[pdf] (ADR-0003)."""
    real_import = importlib.import_module

    def _fake_import(module_name: str, *_args: object, **_kwargs: object) -> ModuleType:
        if module_name == "pymupdf":
            msg = "No module named 'pymupdf'"
            raise ModuleNotFoundError(msg, name="pymupdf")
        return real_import(module_name)

    monkeypatch.setattr(importlib, "import_module", _fake_import)
    with pytest.raises(BackendError, match=re.escape("pip install 'parsecraft[pdf]'")) as excinfo:
        _common.count_pages(_PDF_SOURCE)
    message = str(excinfo.value)
    assert "alongside your 'ocr-*' extra" in message
    assert "ADR-0003" in message
    # The same typed failure surfaces through convert() instead of raising:
    result = _convert(_PDF_SOURCE, _request(_PDF_SOURCE), lambda _number, _req: "x")
    assert result.pages == []
    assert result.failures[0].code is FailureCode.INVALID_INPUT
    assert "parsecraft[pdf]" in result.failures[0].detail


def test_convert_passes_the_context_budget_to_inference(pdf_engine: _PymupdfStub) -> None:
    pdf_engine.page_count = 1
    received: list[int | None] = []

    def _infer(_number: int, req: ConversionRequest) -> str:
        received.append(req.max_context_tokens)
        return "x"

    result = _convert(_PDF_SOURCE, _request(_PDF_SOURCE, max_context_tokens=512), _infer)
    assert received == [512]
    assert result.failures == []


def _convert(
    source: SourceDocument,
    request: ConversionRequest,
    infer_page: Callable[[int, ConversionRequest], str],
) -> BackendResult:
    return _common.convert_pages(
        backend_name="ocr-test",
        backend_version="9.9.9",
        asset=_models.OVIS_ASSET,
        source=source,
        request=request,
        infer_page=infer_page,
    )


# ── _common: optional dependencies and runtimes ─────────────────────────────────


def test_runtime_choice_defaults_and_validates() -> None:
    assert _common.runtime_choice(BackendConfig(name="x")) == "transformers"
    assert _common.runtime_choice(BackendConfig(name="x", options={"runtime": "vllm"})) == "vllm"
    with pytest.raises(BackendError, match="expected one of"):
        _common.runtime_choice(BackendConfig(name="x", options={"runtime": "tensorflow"}))
    with pytest.raises(BackendError, match="must be a string"):
        _common.runtime_choice(BackendConfig(name="x", options={"runtime": 7}))


def test_missing_vllm_runtime_carries_the_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = importlib.import_module

    def _fake_import(module_name: str, *_args: object, **_kwargs: object) -> ModuleType:
        if module_name == "vllm":
            msg = "No module named 'vllm'"
            raise ModuleNotFoundError(msg, name="vllm")
        return real_import(module_name)

    monkeypatch.setattr(importlib, "import_module", _fake_import)
    with pytest.raises(BackendError, match=re.escape("pip install 'parsecraft[vllm]'")):
        _common.load_vllm()


def test_missing_pillow_carries_the_backend_extra_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = importlib.import_module

    def _fake_import(module_name: str, *_args: object, **_kwargs: object) -> ModuleType:
        if module_name == "PIL.Image":
            msg = "No module named 'PIL'"
            raise ModuleNotFoundError(msg, name="PIL")
        return real_import(module_name)

    monkeypatch.setattr(importlib, "import_module", _fake_import)
    with pytest.raises(BackendError, match=re.escape("pip install 'parsecraft[ocr-tele]'")):
        _common.pil_image(_PNG, extra="ocr-tele")


def test_pil_image_decodes_through_the_stub(pil: _PilImageStub) -> None:
    image = _common.pil_image(_PNG, extra="ocr-tele")
    assert isinstance(image, _ImageStub)
    assert image.payload == _PNG


def test_load_vllm_returns_the_stub_runtime(vllm_stub: _VllmStub) -> None:
    assert _common.load_vllm() is vllm_stub


# ── _common: transcriber seams ──────────────────────────────────────────────────


def test_load_transformers_pipeline_passes_pinned_revision() -> None:
    captured: dict[str, object] = {}

    def _factory(**kwargs: object) -> _FakePipeline:
        captured.update(kwargs)
        return _FakePipeline(_TransformersStub())

    pipe = _common.load_transformers_pipeline(
        _factory,
        model_id="org/model",
        model_revision="abc123",
    )
    assert isinstance(pipe, _FakePipeline)
    assert captured["task"] == "image-text-to-text"
    assert captured["model"] == "org/model"
    assert captured["revision"] == "abc123"
    assert captured["device_map"] == "auto"
    assert captured["dtype"] == "auto"
    assert captured["trust_remote_code"] is False

    captured.clear()
    _common.load_transformers_pipeline(
        _factory,
        model_id="org/model",
        model_revision="abc123",
        trust_remote_code=True,
    )
    assert captured["trust_remote_code"] is True


def test_load_transformers_pipeline_maps_load_errors() -> None:
    def _factory(**_kwargs: object) -> _FakePipeline:
        msg = "CUDA out of memory"
        raise RuntimeError(msg)

    with pytest.raises(BackendError, match="failed to load model 'org/model'") as excinfo:
        _common.load_transformers_pipeline(_factory, model_id="org/model", model_revision="abc")
    assert "CUDA out of memory" in str(excinfo.value)


def test_transformers_transcriber_strips_the_echoed_prompt_and_binds_budget(pil: _PilImageStub) -> None:
    state = _TransformersStub()
    state.pipeline_outputs = [
        [{"generated_text": "PROMPT\nANSWER"}],
        [{"generated_text": "plain answer"}],
    ]
    pipe = _FakePipeline(state)
    # Templated prompts end on their own line (add_generation_prompt=True), so the
    # echo and the answer share that boundary — verified live on transformers 5.17.
    transcriber = _common.transformers_transcriber(pipe, prompt="PROMPT\n", image_extra="ocr-ovis")
    assert transcriber(_PNG, None) == "ANSWER"
    assert transcriber(_PNG, 256) == "plain answer"  # no prefix → unchanged
    # transformers rejects raw bytes — the transcriber decodes to PIL first:
    first_image = pipe.calls[0]["images"]
    assert isinstance(first_image, _ImageStub)
    assert first_image.payload == _PNG
    assert pipe.calls[0] == {"text": "PROMPT\n", "images": first_image}
    assert "max_new_tokens" not in pipe.calls[0]
    assert pipe.calls[1]["max_new_tokens"] == 256


def test_chat_prompt_builds_the_image_slot_and_options() -> None:
    tokenizer = _ChatTemplateStub()
    prompt = _common.chat_prompt(tokenizer, user_text="READ THIS", system="Be brief.", enable_thinking=False)
    assert prompt == "TEMPLATED_PROMPT"
    conversation, options = tokenizer.calls[0]
    assert [message["role"] for message in conversation] == ["system", "user"]
    assert options == {"tokenize": False, "add_generation_prompt": True, "enable_thinking": False}
    content = conversation[1]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "image"}
    text_part = content[1]
    assert isinstance(text_part, dict)
    assert text_part["text"] == "READ THIS"


def test_chat_prompt_falls_back_when_template_has_no_thinking_switch() -> None:
    tokenizer = _ChatTemplateStub(accepts_thinking=False)
    prompt = _common.chat_prompt(tokenizer, user_text="READ", enable_thinking=False)
    assert prompt == "TEMPLATED_PROMPT"
    assert len(tokenizer.calls) == 2  # rejected once, retried without the kwarg
    assert "enable_thinking" not in tokenizer.calls[1][1]


def test_chat_prompt_without_thinking_flag_calls_once() -> None:
    tokenizer = _ChatTemplateStub()
    _common.chat_prompt(tokenizer, user_text="READ")
    assert len(tokenizer.calls) == 1
    assert "enable_thinking" not in tokenizer.calls[0][1]


def test_vllm_transcriber_drives_the_engine(vllm_stub: _VllmStub, pil: _PilImageStub) -> None:
    transcriber = _common.vllm_transcriber(
        vllm_stub.engine,
        module=cast("ModuleType", vllm_stub),
        prompt="PAGE PROMPT",
        image_extra="ocr-ovis",
    )
    assert transcriber(_PNG, 128) == "vllm stub page text"
    requests, sampling = vllm_stub.engine.calls[0]
    assert requests[0]["prompt"] == "PAGE PROMPT"
    assert sampling.kwargs["max_tokens"] == 128
    assert sampling.kwargs["temperature"] == 0.0


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ([{"generated_text": "plain"}], "plain"),
        ([{"generated_text": [{"role": "assistant", "content": "chat"}]}], "chat"),
        (
            [{"generated_text": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "last"}]}],
            "last",
        ),
    ],
)
def test_extract_generated_accepts_real_shapes(result: object, expected: str) -> None:
    assert _common.extract_generated(result) == expected


@pytest.mark.parametrize(
    "result",
    [
        "not-a-list",
        [],
        ["not-a-dict"],
        [{"other": 1}],
        [{"generated_text": 42}],
        [{"generated_text": [{"no": "content"}]}],
    ],
)
def test_extract_generated_rejects_garbage(result: object) -> None:
    with pytest.raises(RuntimeError, match="unexpected"):
        _common.extract_generated(result)


# ── Heavy impls under stubs (offline) ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("module", "impl_module", "name", "prompt_prefix", "thinking", "trust_remote_code"),
    [
        (ovis, "parsecraft.backends.ocr._ovis_impl", "ocr-ovis", "Extract all readable content", False, False),
        (tele, "parsecraft.backends.ocr._tele_impl", "ocr-tele", "Please output the text content", None, True),
        (qianfan, "parsecraft.backends.ocr._qianfan_impl", "ocr-qianfan", "Parse this document to Markdown", None, False),
    ],
)
def test_pipeline_backends_convert_under_the_stub(
    module: FactoryModule,
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
    pdf_engine: _PymupdfStub,
    pil: _PilImageStub,
    *,
    impl_module: str,
    name: str,
    prompt_prefix: str,
    thinking: bool | None,
    trust_remote_code: bool,
) -> None:
    pdf_engine.page_count = 3
    state = _TransformersStub()
    state.pipeline_outputs = [
        [{"generated_text": "one"}],
        [{"generated_text": "two"}],
        [{"generated_text": "three"}],
    ]
    impl = impl_loader(impl_module, state)
    backend = impl.create(BackendConfig(name=name))
    assert backend.name == name
    assert backend.capabilities == module.factory.descriptor.capabilities

    analysis = backend.analyze(_PDF_SOURCE)
    assert analysis.page_count == 3

    result = backend.convert(_request(_PDF_SOURCE, max_context_tokens=64))
    assert [page.page_number for page in result.pages] == [1, 2, 3]
    assert result.pages[0].blocks[0].content == "one"
    assert result.failures == []

    pipe = state.last_pipeline
    assert pipe is not None
    first_call = pipe.calls[0]
    # The transcriber receives the CHAT-TEMPLATED prompt, never the raw card prompt:
    assert first_call["text"] == "TEMPLATED_PROMPT"
    assert first_call["max_new_tokens"] == 64

    conversation, options = state.chat_template.calls[0]
    assert options["tokenize"] is False
    assert options["add_generation_prompt"] is True
    assert options.get("enable_thinking") is thinking
    content = conversation[-1]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "image"}
    text_part = content[1]
    assert isinstance(text_part, dict)
    user_text = text_part["text"]
    assert isinstance(user_text, str)
    assert user_text.strip().startswith(prompt_prefix)
    assert state.pipeline_calls[0]["revision"] == _revision_of(module)
    assert state.pipeline_calls[0]["trust_remote_code"] is trust_remote_code


@pytest.mark.parametrize(
    ("impl_module", "revision"),
    [
        ("parsecraft.backends.ocr._ovis_impl", _models.OVIS_REVISION),
        ("parsecraft.backends.ocr._tele_impl", _models.TELE_REVISION),
        ("parsecraft.backends.ocr._qianfan_impl", _models.QIANFAN_REVISION),
    ],
)
def test_pipeline_backends_support_the_vllm_runtime(
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
    pdf_engine: _PymupdfStub,
    pil: _PilImageStub,
    vllm_stub: _VllmStub,
    *,
    impl_module: str,
    revision: str,
) -> None:
    pdf_engine.page_count = 1
    impl = impl_loader(impl_module, _TransformersStub())
    config = BackendConfig(name="ocr-ovis", options={"runtime": "vllm"})
    backend = impl.create(config)
    result = backend.convert(_request(_PDF_SOURCE))
    assert result.pages[0].blocks[0].content == "vllm stub page text"
    assert len(vllm_stub.llm_calls) == 1
    assert vllm_stub.llm_calls[0]["revision"] == revision
    # vLLM gets the same chat-templated prompt (card: apply_chat_template first):
    requests, _sampling = vllm_stub.engine.calls[0]
    assert requests[0]["prompt"] == "TEMPLATED_PROMPT"


@pytest.fixture
def vllm_stub(monkeypatch: pytest.MonkeyPatch) -> _VllmStub:
    """Install the vLLM runtime stub for ``runtime='vllm'`` paths."""
    stub = _VllmStub()
    monkeypatch.setitem(sys.modules, "vllm", stub)
    return stub


def _revision_of(module: FactoryModule) -> str | None:
    asset = module.factory.descriptor.capabilities.model_asset
    return None if asset is None else asset.model_revision


def test_pipeline_load_failure_is_a_typed_error(
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
) -> None:
    state = _TransformersStub()
    state.pipeline_error = RuntimeError("no GPU")
    impl = impl_loader("parsecraft.backends.ocr._ovis_impl", state)
    with pytest.raises(BackendError, match="failed to load model 'ATH-MaaS/OvisOCR2'"):
        impl.create(BackendConfig(name="ocr-ovis"))


def test_pipeline_runtime_error_surfaces_as_typed_error(
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
) -> None:
    state = _TransformersStub()
    state.pipeline_error = RuntimeError("bad revision")
    impl = impl_loader("parsecraft.backends.ocr._tele_impl", state)
    with pytest.raises(BackendError, match="bad revision"):
        impl.create(BackendConfig(name="ocr-tele"))


def test_per_page_pipeline_failure_becomes_a_typed_failure(
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
    pdf_engine: _PymupdfStub,
    pil: _PilImageStub,
) -> None:
    pdf_engine.page_count = 2
    state = _TransformersStub()
    impl = impl_loader("parsecraft.backends.ocr._ovis_impl", state)
    backend = impl.create(BackendConfig(name="ocr-ovis"))
    state.call_error = RuntimeError("mid-run failure")
    result = backend.convert(_request(_PDF_SOURCE))
    assert result.pages == []
    assert [failure.code for failure in result.failures] == [
        FailureCode.BACKEND_ERROR,
        FailureCode.BACKEND_ERROR,
    ]


def test_unlimited_batches_pages_through_infer_multi(
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
    pdf_engine: _PymupdfStub,
) -> None:
    pdf_engine.page_count = 6
    state = _TransformersStub()
    impl = impl_loader("parsecraft.backends.ocr._unlimited_impl", state)
    backend = impl.create(BackendConfig(name="ocr-unlimited"))
    analysis = backend.analyze(_PDF_SOURCE)
    assert analysis.page_count == 6
    result = backend.convert(_request(_PDF_SOURCE, max_context_tokens=99))
    assert [page.page_number for page in result.pages] == [1, 2, 3, 4, 5, 6]
    assert result.failures == []
    # Page 1 batches pages 1-4; pages 2-4 hit the cache; page 5 batches 5-6; page 6 hits it.
    assert [count for count, _prompt, _cap in state.model.calls] == [4, 2]
    assert state.model.calls[0][1] == "<image>Multi page parsing."
    assert state.model.calls[0][2] == 99
    assert state.model.settings == [(1024, 35, 1024), (1024, 35, 1024)]
    assert state.model.output_dir_existed == [True, True]


def test_unlimited_respects_the_batch_size_option(
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
    pdf_engine: _PymupdfStub,
) -> None:
    pdf_engine.page_count = 3
    state = _TransformersStub()
    impl = impl_loader("parsecraft.backends.ocr._unlimited_impl", state)
    backend = impl.create(BackendConfig(name="ocr-unlimited", options={"max_pages_per_call": 1}))
    result = backend.convert(_request(_PDF_SOURCE))
    assert [page.page_number for page in result.pages] == [1, 2, 3]
    assert [count for count, _prompt, _cap in state.model.calls] == [1, 1, 1]


def test_unlimited_rejects_an_invalid_batch_size(
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
    pdf_engine: _PymupdfStub,
) -> None:
    pdf_engine.page_count = 3
    state = _TransformersStub()
    impl = impl_loader("parsecraft.backends.ocr._unlimited_impl", state)
    backend = impl.create(BackendConfig(name="ocr-unlimited", options={"max_pages_per_call": 0}))
    result = backend.convert(_request(_PDF_SOURCE))
    assert result.pages == []
    assert result.failures[0].code is FailureCode.BACKEND_ERROR
    assert "positive integer" in result.failures[0].detail


def test_unlimited_rejects_the_vllm_runtime(
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
) -> None:
    impl = impl_loader("parsecraft.backends.ocr._unlimited_impl", _TransformersStub())
    with pytest.raises(BackendError, match="transformers' only"):
        impl.create(BackendConfig(name="ocr-unlimited", options={"runtime": "vllm"}))


def test_unlimited_reports_model_load_errors(
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
) -> None:
    state = _TransformersStub()
    state.load_error = RuntimeError("weights missing")
    impl = impl_loader("parsecraft.backends.ocr._unlimited_impl", state)
    with pytest.raises(BackendError, match="failed to load model"):
        impl.create(BackendConfig(name="ocr-unlimited"))


def test_unlimited_result_count_mismatch_is_a_typed_failure(
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
    pdf_engine: _PymupdfStub,
) -> None:
    pdf_engine.page_count = 3
    state = _TransformersStub()
    impl = impl_loader("parsecraft.backends.ocr._unlimited_impl", state)
    backend = impl.create(BackendConfig(name="ocr-unlimited"))
    state.model.raw_outputs = ["<PAGE>only-one", "<PAGE>two-a<PAGE>two-b"]
    result = backend.convert(_request(_PDF_SOURCE))
    # Page 1's batch came back short (typed failure), pages 2-3 recover in a new batch.
    assert [page.page_number for page in result.pages] == [2, 3]
    failure = result.failures[0]
    assert failure.code is FailureCode.BACKEND_ERROR
    assert "infer_multi returned 1 page segments for 3 pages" in failure.detail


def test_unlimited_loads_with_trust_remote_code_and_card_settings(
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
) -> None:
    state = _TransformersStub()
    impl = impl_loader("parsecraft.backends.ocr._unlimited_impl", state)
    impl.create(BackendConfig(name="ocr-unlimited"))
    load = state.load_calls[0]
    assert load["trust_remote_code"] is True
    assert load["dtype"] == "bfloat16"
    assert load["use_safetensors"] is True
    assert load["revision"] == _models.UNLIMITED_REVISION
    tokenizer_load = state.tokenizer_load_calls[0]
    assert tokenizer_load["trust_remote_code"] is True
    assert tokenizer_load["revision"] == _models.UNLIMITED_REVISION


def test_unlimited_single_page_without_separator_is_accepted(
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
    pdf_engine: _PymupdfStub,
) -> None:
    pdf_engine.page_count = 1
    state = _TransformersStub()
    impl = impl_loader("parsecraft.backends.ocr._unlimited_impl", state)
    backend = impl.create(BackendConfig(name="ocr-unlimited", options={"max_pages_per_call": 1}))
    state.model.raw_outputs = ["plain text, no separator"]
    result = backend.convert(_request(_PDF_SOURCE))
    assert [page.page_number for page in result.pages] == [1]
    assert result.pages[0].blocks[0].content == "plain text, no separator"
    assert result.failures == []


def test_unlimited_missing_separators_fail_typed_per_batch(
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
    pdf_engine: _PymupdfStub,
) -> None:
    pdf_engine.page_count = 3
    state = _TransformersStub()
    impl = impl_loader("parsecraft.backends.ocr._unlimited_impl", state)
    backend = impl.create(BackendConfig(name="ocr-unlimited"))
    state.model.raw_outputs = ["nothing here", "solo"]
    result = backend.convert(_request(_PDF_SOURCE))
    failure = result.failures[0]
    assert failure.code is FailureCode.BACKEND_ERROR
    assert "infer_multi returned 0 page segments for 3 pages" in failure.detail
    # Page 3 ends up alone (batch of one) and the bare text is accepted:
    assert [page.page_number for page in result.pages] == [3]


def test_unlimited_non_string_output_is_a_typed_failure(
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
    pdf_engine: _PymupdfStub,
) -> None:
    pdf_engine.page_count = 3
    state = _TransformersStub()
    impl = impl_loader("parsecraft.backends.ocr._unlimited_impl", state)
    backend = impl.create(BackendConfig(name="ocr-unlimited"))
    state.model.raw_outputs = [42]
    result = backend.convert(_request(_PDF_SOURCE))
    assert result.pages == []
    failure = result.failures[0]
    assert failure.code is FailureCode.BACKEND_ERROR
    assert "infer_multi returned int, expected str" in failure.detail


@pytest.fixture
def pil(monkeypatch: pytest.MonkeyPatch) -> _PilImageStub:
    """Install the ``PIL.Image`` stub for adapters that decode rasters."""
    image_module = _PilImageStub()
    monkeypatch.setitem(sys.modules, "PIL.Image", image_module)
    return image_module


# ── Fixtures ────────────────────────────────────────────────────────────────────


@pytest.fixture
def pdf_engine(monkeypatch: pytest.MonkeyPatch) -> _PymupdfStub:
    """Install the PDF-engine stub so ``count_pages``/``rasterize_page`` work offline."""
    engine = _PymupdfStub()
    monkeypatch.setitem(sys.modules, "pymupdf", engine)
    return engine


@pytest.fixture
def impl_loader(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str, _TransformersStub], ModuleType]]:
    """Import heavy impl modules against a stub ``transformers``, cleaning up after."""
    loaded: list[str] = []

    def _load(module_name: str, transformers: _TransformersStub) -> ModuleType:
        monkeypatch.setitem(sys.modules, "transformers", transformers)
        sys.modules.pop(module_name, None)  # force a fresh top-level import
        module = importlib.import_module(module_name)
        loaded.append(module_name)
        return module

    yield _load
    for module_name in loaded:
        sys.modules.pop(module_name, None)


def _request(
    source: SourceDocument,
    *,
    page_range: tuple[int, int] | None = None,
    timeout_s: float | None = None,
    cancellation: Callable[[], bool] | None = None,
    max_output_chars: int | None = None,
    max_context_tokens: int | None = None,
) -> ConversionRequest:
    bounds = PageRange(start=page_range[0], end=page_range[1]) if page_range is not None else None
    return ConversionRequest(
        source=source,
        page_range=bounds,
        timeout_s=timeout_s,
        cancellation=cancellation,
        max_output_chars=max_output_chars,
        max_context_tokens=max_context_tokens,
    )
