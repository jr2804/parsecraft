"""OCR/VLM backend adapters: descriptors, light/heavy boundary, bounds, failures.

Fully offline: the heavy ``transformers``/``vllm``/``PIL``/``pymupdf`` surfaces
are stubbed through ``sys.modules`` before the impl modules are imported, and
nothing here may touch the network or download a model.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import sys
import time
import tomllib
from collections.abc import Callable, Iterator
from io import BytesIO
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import pytest
from pydantic import ValidationError

from parsecraft.assets.errors import OfflineModeError
from parsecraft.assets.models import AssetPin
from parsecraft.backends import registry as registry_module
from parsecraft.backends.errors import BackendError, DependencyUnavailableError, UnsupportedDependencyVersionError
from parsecraft.backends.ocr import _common, _models, ovis, qianfan, tele, unlimited
from parsecraft.backends.ocr._common import VllmCompletion, VllmOutput
from parsecraft.backends.protocol import (
    AssetFilePin,
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


_PINNED_ASSETS = (
    (_models.OVIS_ASSET, "ovis"),
    (_models.TELE_ASSET, "tele"),
    (_models.UNLIMITED_ASSET, "unlimited"),
    (_models.QIANFAN_ASSET, "qianfan"),
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
        return [_VllmOutputStub(self.text)]


class _VllmStub:
    """Offline stand-in for the optional vLLM runtime (extra ``vllm``)."""

    SamplingParams = _SamplingParamsStub

    def __init__(self) -> None:
        self.engine = _EngineStub()
        self.llm_calls: list[dict[str, object]] = []

    def LLM(self, **kwargs: object) -> _EngineStub:  # noqa: N802 — mirrors the vLLM API
        self.llm_calls.append(kwargs)
        return self.engine


class _CudaDevice:
    """Stand-in for ``torch.device('cuda')`` — the CUDA check only reads ``.type``."""

    type = "cuda"

    def __repr__(self) -> str:
        return "device(type='cuda')"


class _CpuDevice:
    """Stand-in for ``torch.device('cpu')`` (the silent-fallback case)."""

    type = "cpu"

    def __repr__(self) -> str:
        return "device(type='cpu')"


class _FakePipeline:
    def __init__(self, state: _TransformersStub) -> None:
        self._state = state
        self.tokenizer = state.chat_template
        self.calls: list[dict[str, object]] = []
        # The real pipelines expose the resolved device; the GPU-required
        # backends are rejected when it is not CUDA, so the stub models a GPU.
        self.device = _CudaDevice()

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


class _AutoProcessorStub:
    def __init__(self, state: _TransformersStub) -> None:
        self._state = state

    def from_pretrained(self, source: str, **kwargs: object) -> object:
        self._state.processor_load_calls.append({"source": source, **kwargs})
        if self._state.processor_error is not None:
            raise self._state.processor_error
        return object()


class _VendoredTeleModule:
    Qwen2_5_VLForConditionalGeneration: object = None


class _VendoredUnlimitedModule:
    UnlimitedOCRConfig: object = None
    UnlimitedOCRForCausalLM: object = None


class _FakeEvalModel:
    """Stands in for UnlimitedOCRForCausalLM: eval() + the infer_multi seam."""

    def __init__(self, state: _TransformersStub) -> None:
        self._state = state
        # A loaded ``PreTrainedModel`` carries the placement the CUDA check reads.
        self.device = _CudaDevice()

    def eval(self) -> _FakeEvalModel:
        return self

    def infer_multi(self, *args: object, **kwargs: object) -> tuple[object, int]:
        return self._state.model.infer_multi(*args, **kwargs)  # ty: ignore[invalid-argument-type]


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
        self.AutoProcessor = _AutoProcessorStub(self)
        self.tokenizer_load_calls: list[dict[str, object]] = []
        self.tokenizer_error: Exception | None = None
        self.chat_template = _ChatTemplateStub()
        # Vendored-class load records (pc-4u7.36: no trust_remote_code paths)
        self.processor_load_calls: list[dict[str, object]] = []
        self.processor_error: Exception | None = None
        self.vendored_model_loads: list[dict[str, object]] = []
        self.vendored_config_loads: list[dict[str, object]] = []

    def pipeline(self, **kwargs: object) -> _FakePipeline:
        self.pipeline_calls.append(dict(kwargs))
        if self.pipeline_error is not None:
            raise self.pipeline_error
        self.last_pipeline = _FakePipeline(self)
        return self.last_pipeline


class _SentinelBackend:
    """What a fake impl's create() returns — identity-checked only."""

    name = "sentinel"


# ── Asset-manager wiring (pc-4u7.21) and the pin catalogue (pc-4u7.22) ──────────


class _AssetManagerSpy:
    """Recording AssetManager stand-in — no network can happen in offline tests."""

    instances: list[_AssetManagerSpy] = []
    error: Exception | None = None

    def __init__(self, *, offline: bool = False) -> None:
        self.offline = offline
        self.pins: list[AssetPin] = []
        type(self).instances.append(self)

    def ensure(self, pin: AssetPin) -> list[str]:
        self.pins.append(pin)
        if _AssetManagerSpy.error is not None:
            raise _AssetManagerSpy.error
        return [f"fake-assets/{name}" for name in pin.filenames]

    @staticmethod
    def revision_dir(model_id: str, revision: str) -> Path:
        return Path("fake-assets") / model_id.replace("/", "--") / revision[:12]


# ── _common: transcriber seams ──────────────────────────────────────────────────


class _ProcessorLoader:
    """Records a processor/tokenizer load request — the tokenizer fix lives here."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.result: object = object()

    def __call__(self, source: str, **kwargs: object) -> object:
        self.calls.append((source, dict(kwargs)))
        return self.result


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
    assert capabilities.gpu_requirement == 1.0
    assert capabilities.estimated_vram_gb == vram
    assert capabilities.optional_dependency_group == extra
    assert capabilities.supports_page_ranges is True
    assert capabilities.supports_multi_page is multi_page
    assert capabilities.supported_formats == ["application/pdf", "image/jpeg", "image/png"]
    assert module.DESCRIPTOR is descriptor


def test_transformers_range_guard_accepts_the_unified_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_common, "_package_version", lambda _name: "5.17.0")
    _common.require_transformers()  # no raise == satisfied


def test_transformers_range_guard_rejects_out_of_range_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_common, "_package_version", lambda _name: "4.40.0")
    with pytest.raises(UnsupportedDependencyVersionError, match=re.escape("transformers==4.40.0 does not satisfy the required range '>=5.17,<6'")) as excinfo:
        _common.require_transformers()
    assert (excinfo.value.package, excinfo.value.actual, excinfo.value.expected) == ("transformers", "4.40.0", ">=5.17,<6")
    assert isinstance(excinfo.value, BackendError)


def test_transformers_range_guard_rejects_unparseable_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_common, "_package_version", lambda _name: "not-a-version")
    with pytest.raises(UnsupportedDependencyVersionError) as excinfo:
        _common.require_transformers()
    assert excinfo.value.actual == "not-a-version"


def test_transformers_range_matches_the_pyproject_extras() -> None:
    """One window everywhere: the runtime guard and all four OCR extras agree."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    expected = f"transformers{_models.TRANSFORMERS_RANGE}"
    for name in ("ocr-ovis", "ocr-qianfan", "ocr-tele", "ocr-unlimited"):
        assert expected in extras[name], f"{name} missing unified range {expected!r}"


def test_unpinned_tele_descriptor_keeps_the_hub_revision(
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pc-4u7.36: the vendored loaders still honor a hub id + pinned revision."""
    state = _TransformersStub()
    impl = impl_loader("parsecraft.backends.ocr._tele_impl", state)
    monkeypatch.setattr(impl, "TELE_ASSET", _models.TELE_ASSET.model_copy(update={"file_pins": ()}))
    impl.create(BackendConfig(name="ocr-tele"))
    model_load = state.vendored_model_loads[0]
    assert model_load["source"] == _models.TELE_MODEL_ID
    assert model_load["revision"] == _models.TELE_REVISION
    processor_load = state.processor_load_calls[0]
    assert processor_load["source"] == _models.TELE_MODEL_ID
    assert processor_load["revision"] == _models.TELE_REVISION
    assert processor_load["fix_mistral_regex"] is True


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
    with pytest.raises(
        DependencyUnavailableError,
        match=re.escape(f"backend dependency 'transformers' is not installed — install the '{extra}' extra"),
    ) as excinfo:
        module.factory(BackendConfig(name=name))
    assert excinfo.value.module == "transformers"
    assert excinfo.value.extra == extra
    assert isinstance(excinfo.value, BackendError)


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
    with pytest.raises(
        DependencyUnavailableError,
        match=re.escape("backend dependency 'vllm' is not installed — install the 'vllm' extra"),
    ) as excinfo:
        _common.load_vllm()
    assert (excinfo.value.module, excinfo.value.extra) == ("vllm", "vllm")


def test_missing_pillow_carries_the_backend_extra_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = importlib.import_module

    def _fake_import(module_name: str, *_args: object, **_kwargs: object) -> ModuleType:
        if module_name == "PIL.Image":
            msg = "No module named 'PIL'"
            raise ModuleNotFoundError(msg, name="PIL")
        return real_import(module_name)

    monkeypatch.setattr(importlib, "import_module", _fake_import)
    with pytest.raises(
        DependencyUnavailableError,
        match=re.escape("backend dependency 'PIL' is not installed — install the 'ocr-tele' extra"),
    ) as excinfo:
        _common.pil_image(_PNG, extra="ocr-tele")
    assert (excinfo.value.module, excinfo.value.extra) == ("PIL", "ocr-tele")


def test_pil_image_decodes_through_the_stub(pil: _PilImageStub) -> None:
    image = _common.pil_image(_PNG, extra="ocr-tele")
    assert isinstance(image, _ImageStub)
    assert image.payload == _PNG


def test_load_vllm_returns_the_stub_runtime(vllm_stub: _VllmStub) -> None:
    assert _common.load_vllm() is vllm_stub


def test_tokenizer_load_kwargs_carry_the_mistral_regex_fix() -> None:
    """One home for the tokenizer correction, merged with caller kwargs."""
    assert _common.tokenizer_load_kwargs() == {"fix_mistral_regex": True}
    assert _common.tokenizer_load_kwargs(revision="abc") == {"fix_mistral_regex": True, "revision": "abc"}


def test_tokenizer_load_kwargs_are_merged_not_shared() -> None:
    """A caller mutating its own dict must not change the next load's flags."""
    mine = _common.tokenizer_load_kwargs(revision="abc")
    mine["fix_mistral_regex"] = False
    assert _common.tokenizer_load_kwargs() == {"fix_mistral_regex": True}


def test_load_transformers_pipeline_loads_the_processor_with_the_fix() -> None:
    """The pipeline's own processor load cannot carry the fix, so we load it.

    ``pipeline()`` resolves the processor with only its hub/model kwargs, so a
    flag passed to it never reaches the tokenizer; an instance we built does.
    """
    captured: dict[str, object] = {}
    loader = _ProcessorLoader()

    def _factory(**kwargs: object) -> _FakePipeline:
        captured.update(kwargs)
        return _FakePipeline(_TransformersStub())

    pipe = _common.load_transformers_pipeline(
        _factory,
        processor_loader=loader,
        model_source="org/model",
        model_revision="abc123",
    )
    assert isinstance(pipe, _FakePipeline)
    assert loader.calls == [("org/model", {"fix_mistral_regex": True, "trust_remote_code": False, "revision": "abc123"})]
    assert captured["processor"] is loader.result  # the instance, never a string identifier


def test_load_transformers_pipeline_passes_pinned_revision() -> None:
    captured: dict[str, object] = {}

    def _factory(**kwargs: object) -> _FakePipeline:
        captured.update(kwargs)
        return _FakePipeline(_TransformersStub())

    loader = _ProcessorLoader()
    pipe = _common.load_transformers_pipeline(
        _factory,
        processor_loader=loader,
        model_source="org/model",
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
        processor_loader=loader,
        model_source="org/model",
        model_revision="abc123",
        trust_remote_code=True,
    )
    assert captured["trust_remote_code"] is True

    # Managed local dir: no hub revision may leak into the load kwargs.
    captured.clear()
    loader.calls.clear()
    _common.load_transformers_pipeline(
        _factory,
        processor_loader=loader,
        model_source="fake-assets/org--model",
        model_revision=None,
    )
    assert "revision" not in captured
    assert loader.calls == [("fake-assets/org--model", {"fix_mistral_regex": True, "trust_remote_code": False})]


def test_load_transformers_pipeline_maps_load_errors() -> None:
    def _factory(**_kwargs: object) -> _FakePipeline:
        msg = "CUDA out of memory"
        raise RuntimeError(msg)

    with pytest.raises(BackendError, match="failed to load model 'org/model'") as excinfo:
        _common.load_transformers_pipeline(
            _factory,
            processor_loader=_ProcessorLoader(),
            model_source="org/model",
            model_revision="abc",
        )
    assert "CUDA out of memory" in str(excinfo.value)


def test_require_cuda_device_rejects_a_silent_cpu_fallback() -> None:
    """``device_map='auto'`` picks the CPU when CUDA is unusable: refuse, typed.

    A 9.5 GB VLM generating token-by-token on the CPU is not a slow conversion
    but an unusable one, so a GPU-hard backend fails here instead of running for
    hours — the planner can then fall back to a CPU backend.
    """
    state = _TransformersStub()

    def _cpu_factory(**_kwargs: object) -> _FakePipeline:
        pipe = _FakePipeline(state)
        pipe.device = _CpuDevice()  # ty: ignore[invalid-assignment] — placement fixture
        return pipe

    with pytest.raises(BackendError, match="declares a hard GPU requirement") as excinfo:
        _common.load_transformers_pipeline(
            _cpu_factory,
            processor_loader=_ProcessorLoader(),
            model_source="org/model",
            model_revision=None,
            require_gpu=True,
        )
    assert "loaded on device(type='cpu')" in str(excinfo.value)
    # The same placement is fine for a backend that does not require a GPU:
    assert (
        _common.load_transformers_pipeline(
            _cpu_factory,
            processor_loader=_ProcessorLoader(),
            model_source="org/model",
            model_revision=None,
        )
        is not None
    )


def test_require_cuda_device_accepts_a_cuda_placement_and_rejects_a_device_less_holder() -> None:
    _common.require_cuda_device(_FakePipeline(_TransformersStub()), model_source="org/model")
    with pytest.raises(BackendError, match="loaded on None"):
        _common.require_cuda_device(object(), model_source="org/model")


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
    assert pipe.calls[0] == {"text": "PROMPT\n", "images": first_image, "max_new_tokens": _common.DEFAULT_PAGE_MAX_NEW_TOKENS}
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


def test_vllm_transcriber_pins_the_page_budget_when_the_request_names_none(vllm_stub: _VllmStub, pil: _PilImageStub) -> None:
    """Both runtimes share ONE default budget, so a page cannot differ per runtime."""
    transcriber = _common.vllm_transcriber(
        vllm_stub.engine,
        module=cast("ModuleType", vllm_stub),
        prompt="PAGE PROMPT",
        image_extra="ocr-ovis",
    )
    assert transcriber(_PNG, None) == "vllm stub page text"
    _, sampling = vllm_stub.engine.calls[0]
    assert sampling.kwargs["max_tokens"] == _common.DEFAULT_PAGE_MAX_NEW_TOKENS


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
        (tele, "parsecraft.backends.ocr._tele_impl", "ocr-tele", "Please output the text content", None, None),
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
    pipeline_call = state.pipeline_calls[0]
    # The tokenizer-regex fix is applied where the tokenizer is actually loaded
    # (pc-scr): the processor for the pipeline backends, the tokenizer for the
    # vendored ones. ``pipeline()`` itself cannot forward the flag, so the
    # pipeline must receive a processor INSTANCE instead of a path.
    processor_load = state.processor_load_calls[0]
    assert processor_load["fix_mistral_regex"] is True
    if trust_remote_code is None:
        # Vendored branch (tele, pc-4u7.36): explicit model class + processor,
        # no revision on the local dir, and trust_remote_code is gone.
        model_load = state.vendored_model_loads[0]
        assert str(model_load["source"]).startswith("fake-assets")
        assert "revision" not in model_load
        assert str(processor_load["source"]).startswith("fake-assets")
        assert "trust_remote_code" not in pipeline_call
    else:
        # Assets are managed: the pipeline loads the verified LOCAL dir, no hub revision.
        assert str(pipeline_call["model"]).startswith("fake-assets")
        assert "revision" not in pipeline_call
        assert pipeline_call["trust_remote_code"] is trust_remote_code
        if trust_remote_code is False:
            # The pipeline backends get the processor we loaded, not a path to load.
            assert not isinstance(pipeline_call["processor"], str)


@pytest.mark.parametrize(
    "impl_module",
    [
        "parsecraft.backends.ocr._ovis_impl",
        "parsecraft.backends.ocr._tele_impl",
        "parsecraft.backends.ocr._qianfan_impl",
    ],
)
def test_pipeline_backends_support_the_vllm_runtime(
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
    pdf_engine: _PymupdfStub,
    pil: _PilImageStub,
    vllm_stub: _VllmStub,
    *,
    impl_module: str,
) -> None:
    pdf_engine.page_count = 1
    impl = impl_loader(impl_module, _TransformersStub())
    config = BackendConfig(name="ocr-ovis", options={"runtime": "vllm"})
    backend = impl.create(config)
    result = backend.convert(_request(_PDF_SOURCE))
    assert result.pages[0].blocks[0].content == "vllm stub page text"
    assert len(vllm_stub.llm_calls) == 1
    engine_call = vllm_stub.llm_calls[0]
    assert str(engine_call["model"]).startswith("fake-assets")
    assert "revision" not in engine_call
    # vLLM gets the same chat-templated prompt (card: apply_chat_template first):
    requests, _sampling = vllm_stub.engine.calls[0]
    assert requests[0]["prompt"] == "TEMPLATED_PROMPT"


def test_pipeline_load_failure_is_a_typed_error(
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
) -> None:
    state = _TransformersStub()
    state.pipeline_error = RuntimeError("no GPU")
    impl = impl_loader("parsecraft.backends.ocr._ovis_impl", state)
    with pytest.raises(BackendError, match="failed to load model"):
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


def test_unlimited_loads_vendored_classes_without_remote_code(
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
) -> None:
    """pc-4u7.36: explicit vendored config+model classes, trust_remote_code gone."""
    state = _TransformersStub()
    impl = impl_loader("parsecraft.backends.ocr._unlimited_impl", state)
    impl.create(BackendConfig(name="ocr-unlimited"))
    config_load = state.vendored_config_loads[0]
    model_load = state.vendored_model_loads[0]
    assert str(config_load["source"]).startswith("fake-assets")
    assert str(model_load["source"]).startswith("fake-assets")
    assert model_load["dtype"] == "bfloat16"
    assert model_load["use_safetensors"] is True
    assert model_load["device_map"] == "auto"
    assert "config" in model_load  # explicit config object, no auto-class resolution
    assert "trust_remote_code" not in config_load
    assert "trust_remote_code" not in model_load
    assert "revision" not in config_load
    assert "revision" not in model_load
    tokenizer_load = state.tokenizer_load_calls[0]
    assert "trust_remote_code" not in tokenizer_load
    assert "revision" not in tokenizer_load


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


@pytest.fixture(autouse=True)
def managed_assets(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Route every test in this file through a fake AssetManager (offline by construction)."""
    _AssetManagerSpy.instances.clear()
    _AssetManagerSpy.error = None
    monkeypatch.setattr(_common, "AssetManager", _AssetManagerSpy)
    # The transformers version guard must not need transformers installed:
    monkeypatch.setattr(_common, "_package_version", lambda _name: "5.17.0")
    yield
    _AssetManagerSpy.instances.clear()
    _AssetManagerSpy.error = None


def test_ensure_assets_maps_descriptor_pins_onto_an_asset_pin() -> None:
    local = _common.ensure_assets(_models.OVIS_ASSET, BackendConfig(name="ocr-ovis"))
    assert local is not None
    assert local.startswith("fake-assets")
    spy = _AssetManagerSpy.instances[-1]
    assert spy.offline is False
    pin = spy.pins[0]
    assert pin.descriptor is _models.OVIS_ASSET
    assert pin.filenames == [file_pin.path for file_pin in _models.OVIS_ASSET.file_pins]
    assert pin.expected_sha256 == {file_pin.path: file_pin.sha256 for file_pin in _models.OVIS_ASSET.file_pins}


def test_unpinned_descriptor_keeps_the_legacy_hub_path() -> None:
    unpinned = _models.OVIS_ASSET.model_copy(update={"file_pins": ()})
    local = _common.ensure_assets(unpinned, BackendConfig(name="ocr-ovis"))
    assert local is None
    assert _AssetManagerSpy.instances == []  # the manager is never even constructed


def test_offline_option_is_validated_and_forwarded() -> None:
    local = _common.ensure_assets(
        _models.OVIS_ASSET,
        BackendConfig(name="ocr-ovis", options={"offline": True}),
    )
    assert local is not None
    assert _AssetManagerSpy.instances[-1].offline is True
    with pytest.raises(BackendError, match="must be a boolean"):
        _common.ensure_assets(
            _models.OVIS_ASSET,
            BackendConfig(name="ocr-ovis", options={"offline": "yes"}),
        )


def test_asset_manager_failures_propagate_typed() -> None:
    _AssetManagerSpy.error = OfflineModeError("m", "rev", "offline mode and files not cached")
    with pytest.raises(OfflineModeError):
        _common.ensure_assets(_models.OVIS_ASSET, BackendConfig(name="ocr-ovis"))


def test_model_source_falls_back_to_hub_with_the_pinned_revision() -> None:
    unpinned = _models.OVIS_ASSET.model_copy(update={"file_pins": ()})
    source, revision = _common.model_source_and_revision(unpinned, BackendConfig(name="ocr-ovis"))
    assert (source, revision) == (_models.OVIS_MODEL_ID, _models.OVIS_REVISION)


@pytest.mark.parametrize(("asset", "recorded"), _PINNED_ASSETS)
def test_file_pins_match_the_recorded_hf_tree(asset: ModelAssetDescriptor, recorded: str) -> None:
    """Pins must equal the recorded HF tree API response (offline transcription check)."""
    raw = json.loads(Path(f"tests/fixtures/hf_tree/hf_tree_{recorded}.json").read_text(encoding="utf-8"))
    expected = [(entry["path"], entry["sha256"], entry["size"]) for entry in raw]
    assert [(pin.path, pin.sha256, pin.size) for pin in asset.file_pins] == expected


@pytest.mark.parametrize(("asset", "_recorded"), _PINNED_ASSETS)
def test_file_pins_are_flat_verified_runtime_files(asset: ModelAssetDescriptor, _recorded: str) -> None:
    paths = [pin.path for pin in asset.file_pins]
    assert paths, "every pinned model needs a non-empty file manifest"
    assert len(paths) == len(set(paths))
    for pin in asset.file_pins:
        assert "/" not in pin.path  # flat layout: from_pretrained(local_dir) finds every file
        assert re.fullmatch(r"[0-9a-f]{64}", pin.sha256)
        assert pin.size is not None
        assert pin.size > 0
        assert not pin.path.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".pdf", ".md"))


def test_asset_file_pin_rejects_a_bad_sha256() -> None:
    with pytest.raises(ValidationError, match="pattern"):
        AssetFilePin(path="config.json", sha256="not-a-hash", size=12)


# ── Hub fallback: unpinned descriptors keep id + revision on every load path ─────


@pytest.mark.parametrize(
    ("asset_attr", "impl_module", "expected_id", "expected_revision"),
    [
        ("OVIS_ASSET", "parsecraft.backends.ocr._ovis_impl", _models.OVIS_MODEL_ID, _models.OVIS_REVISION),
        ("TELE_ASSET", "parsecraft.backends.ocr._tele_impl", _models.TELE_MODEL_ID, _models.TELE_REVISION),
        (
            "QIANFAN_ASSET",
            "parsecraft.backends.ocr._qianfan_impl",
            _models.QIANFAN_MODEL_ID,
            _models.QIANFAN_REVISION,
        ),
    ],
)
def test_unpinned_descriptors_keep_the_hub_revision_on_the_vllm_path(
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
    vllm_stub: _VllmStub,
    monkeypatch: pytest.MonkeyPatch,
    *,
    asset_attr: str,
    impl_module: str,
    expected_id: str,
    expected_revision: str,
) -> None:
    impl = impl_loader(impl_module, _TransformersStub())
    asset = getattr(_models, asset_attr)
    monkeypatch.setattr(impl, asset_attr, asset.model_copy(update={"file_pins": ()}))
    impl.create(BackendConfig(name="ocr-ovis", options={"runtime": "vllm"}))
    engine_call = vllm_stub.llm_calls[0]
    assert engine_call["model"] == expected_id
    assert engine_call["revision"] == expected_revision


@pytest.fixture
def vllm_stub(monkeypatch: pytest.MonkeyPatch) -> _VllmStub:
    """Install the vLLM runtime stub for ``runtime='vllm'`` paths."""
    stub = _VllmStub()
    monkeypatch.setitem(sys.modules, "vllm", stub)
    return stub


def test_unpinned_unlimited_descriptor_keeps_the_hub_revision(
    impl_loader: Callable[[str, _TransformersStub], ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _TransformersStub()
    impl = impl_loader("parsecraft.backends.ocr._unlimited_impl", state)
    monkeypatch.setattr(
        impl,
        "UNLIMITED_ASSET",
        _models.UNLIMITED_ASSET.model_copy(update={"file_pins": ()}),
    )
    impl.create(BackendConfig(name="ocr-unlimited"))
    config_load = state.vendored_config_loads[0]
    model_load = state.vendored_model_loads[0]
    assert config_load["source"] == _models.UNLIMITED_MODEL_ID
    assert config_load["revision"] == _models.UNLIMITED_REVISION
    assert model_load["source"] == _models.UNLIMITED_MODEL_ID
    assert model_load["revision"] == _models.UNLIMITED_REVISION
    tokenizer_load = state.tokenizer_load_calls[0]
    assert tokenizer_load["revision"] == _models.UNLIMITED_REVISION
    assert tokenizer_load["fix_mistral_regex"] is True


@pytest.fixture
def impl_loader(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str, _TransformersStub], ModuleType]]:
    """Import heavy impl modules against a stub ``transformers``, cleaning up after."""
    loaded: list[str] = []

    def _load(module_name: str, transformers: _TransformersStub) -> ModuleType:
        monkeypatch.setitem(sys.modules, "transformers", transformers)
        monkeypatch.setitem(
            sys.modules,
            "parsecraft.backends.ocr._vendored.naviocr.modeling_naviocr",
            _naviocr_vendored_stub(transformers),
        )
        monkeypatch.setitem(
            sys.modules,
            "parsecraft.backends.ocr._vendored.unlimited.modeling_unlimitedocr",
            _unlimited_vendored_stub(transformers),
        )
        sys.modules.pop(module_name, None)  # force a fresh top-level import
        module = importlib.import_module(module_name)
        loaded.append(module_name)
        return module

    yield _load
    for module_name in loaded:
        sys.modules.pop(module_name, None)


def _naviocr_vendored_stub(state: _TransformersStub) -> object:
    """Stand-in for `_vendored.naviocr.modeling_naviocr` (explicit-class loads)."""

    class _VendoredTeleModel:
        @staticmethod
        def from_pretrained(source: str, **kwargs: object) -> object:
            state.vendored_model_loads.append({"source": source, **kwargs})
            if state.load_error is not None:
                raise state.load_error
            return object()

    stub = _VendoredTeleModule()
    stub.Qwen2_5_VLForConditionalGeneration = _VendoredTeleModel  # type: ignore[attr-defined]
    return stub


def _unlimited_vendored_stub(state: _TransformersStub) -> object:
    """Stand-in for `_vendored.unlimited.modeling_unlimitedocr`."""

    class _Config:
        @staticmethod
        def from_pretrained(source: str, **kwargs: object) -> object:
            state.vendored_config_loads.append({"source": source, **kwargs})
            if state.load_error is not None:
                raise state.load_error
            return object()

    class _Model:
        @staticmethod
        def from_pretrained(source: str, *, config: object, **kwargs: object) -> object:
            state.vendored_model_loads.append({"source": source, "config": config, **kwargs})
            if state.load_error is not None:
                raise state.load_error
            return _FakeEvalModel(state)

    stub = _VendoredUnlimitedModule()
    stub.UnlimitedOCRConfig = _Config  # type: ignore[attr-defined]
    stub.UnlimitedOCRForCausalLM = _Model  # type: ignore[attr-defined]
    return stub
