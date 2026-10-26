"""MinerU backend: light/heavy boundary, engine loading, two-step convert, bounds.

Fully offline: the heavy ``transformers`` surface is stubbed through
``sys.modules`` before ``_impl`` loads (the docling/ocr pattern), the base
``pypdfium2`` engine is faked at ``_impl.pdfium`` with scripted pages, and the
raster seam is patched to an in-memory PNG (Pillow is a base dep). Nothing
here touches the network or loads a model.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Iterator
from io import BytesIO
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from PIL import Image

from parsecraft.backends.errors import BackendError, DependencyUnavailableError
from parsecraft.backends.mineru import mineru as mineru_module
from parsecraft.backends.protocol import BackendConfig, ConversionRequest, DocumentBackend, SourceDocument
from parsecraft.ir.models import FailureCode, PageRange, PassKind
from parsecraft.routing.models import RoutingConstraints
from parsecraft.routing.rules import is_hard_eligible

_PDF_BYTES = b"%PDF-1.7 stub"
_PDF_SOURCE = SourceDocument(uri="file:///doc.pdf", media_type="application/pdf", content=_PDF_BYTES)
_IMPL_MODULE = "parsecraft.backends.mineru._impl"


def _png() -> bytes:
    image = Image.new("RGB", (120, 160), (255, 255, 255))
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


# ── Fake pypdfium2: scripted pages (text layer, size) ─────────────────────────


class _FakeTextPage:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_text_range(self) -> str:
        return self._text

    @staticmethod
    def close() -> None:
        return None


class _FakePage:
    def __init__(self, text: str, size: tuple[float, float]) -> None:
        self._text = text
        self._size = size

    def get_textpage(self) -> _FakeTextPage:
        return _FakeTextPage(self._text)

    def get_size(self) -> tuple[float, float]:
        return self._size


class _FakeDocument:
    pages: list[tuple[str, tuple[float, float]]] = []

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __len__(self) -> int:
        return len(type(self).pages)

    def __getitem__(self, index: int) -> _FakePage:
        return _FakePage(*type(self).pages[index])

    @staticmethod
    def close() -> None:
        return None


def _fake_pdfium(pages: list[tuple[str, tuple[float, float]]]) -> SimpleNamespace:
    _FakeDocument.pages = pages
    return SimpleNamespace(PdfDocument=_FakeDocument)


# ── Stub transformers (offline stand-in; never the real package) ──────────────


class _FakeTextConfig:
    tie_word_embeddings: bool | None = True


class _FakeConfig:
    def __init__(self, text_config: _FakeTextConfig | None = None) -> None:
        self.text_config = text_config or _FakeTextConfig()
        self.tie_word_embeddings: bool | None = None

    def get_text_config(self, *, decoder: bool = False) -> _FakeTextConfig:
        return self.text_config


class _AutoConfigStub:
    def __init__(self, state: _TransformersStub) -> None:
        self._state = state

    @staticmethod
    def from_pretrained(source: str, revision: str | None = None) -> _FakeConfig:
        config = _FakeConfig()
        config.__dict__["_source"] = source  # recorded, never read by the impl
        config.__dict__["_revision"] = revision
        state = _TransformersStub.current
        if state is not None:
            state.configs.append(config)
        return config


class _PretrainedConfigStub:
    @staticmethod
    def get_config_dict(source: str, revision: str | None = None) -> tuple[dict[str, object], dict[str, object]]:
        return _TransformersStub.raw_config, {}


class _ModelStub:
    def __init__(self, state: _TransformersStub) -> None:
        self._state = state
        self.device = "cpu"
        self.dtype = "float32"
        self.generation_config: SimpleNamespace | None = SimpleNamespace(bos_token_id=0, eos_token_id=[1, 2], pad_token_id=None)
        self.config: _FakeConfig | None = None

    @staticmethod
    def from_pretrained(source: str, **kwargs: object) -> _ModelStub:
        state = _TransformersStub.current
        assert state is not None
        state.model_loads.append({"source": source, **kwargs})
        if state.load_error is not None:
            raise state.load_error
        return _ModelStub(state)


class _ProcessorStub:
    def __init__(self, state: _TransformersStub) -> None:
        self._state = state
        self.image_processor: object | None = None
        self.tokenizer = SimpleNamespace(pad_token_id=3)

    @staticmethod
    def apply_chat_template(conversation: object, **options: object) -> str:
        return "CHAT"

    def __call__(self, **kwargs: object) -> dict[str, object]:
        self._state.processor_calls.append(kwargs)
        return {"input_ids": _FakeTensor([[1, 2, 3]]), "pixel_values": _FakeTensor([[0]])}

    @staticmethod
    def batch_decode(sequences: object, **options: object) -> list[str]:
        return [str(sequences)]


class _AutoProcessorStub:
    def __init__(self, state: _TransformersStub) -> None:
        self._state = state

    @staticmethod
    def from_pretrained(source: str, revision: str | None = None, **kwargs: object) -> _ProcessorStub:
        state = _TransformersStub.current
        assert state is not None
        state.processor_loads.append({"source": source, "revision": revision, **kwargs})
        if state.processor_error is not None:
            raise state.processor_error
        return _ProcessorStub(state)


class _AutoImageProcessorStub:
    def __init__(self, state: _TransformersStub) -> None:
        self._state = state

    @staticmethod
    def from_pretrained(source: str, backend: str = "", revision: str | None = None) -> SimpleNamespace:
        state = _TransformersStub.current
        assert state is not None
        state.image_processor_loads.append({"source": source, "backend": backend, "revision": revision})
        return SimpleNamespace(backend=backend)


class _FakeTensor:
    def __init__(self, rows: list[list[int]]) -> None:
        self._rows = rows

    def to(self, *, device: object = ..., dtype: object = ...) -> _FakeTensor:
        return self

    def tolist(self) -> list[list[int]]:
        return self._rows


class _TransformersStub(ModuleType):
    """Offline stand-in for the ``transformers`` package."""

    current: _TransformersStub | None = None
    raw_config: dict[str, object] = {"tie_word_embeddings": True}

    def __init__(self) -> None:
        super().__init__("transformers")
        self.model_loads: list[dict[str, object]] = []
        self.processor_loads: list[dict[str, object]] = []
        self.image_processor_loads: list[dict[str, object]] = []
        self.configs: list[_FakeConfig] = []
        self.processor_calls: list[object] = []
        self.load_error: Exception | None = None
        self.processor_error: Exception | None = None
        self.AutoConfig = _AutoConfigStub
        self.PretrainedConfig = _PretrainedConfigStub
        self.Qwen2VLForConditionalGeneration = _ModelStub
        self.AutoProcessor = _AutoProcessorStub
        self.AutoImageProcessor = _AutoImageProcessorStub


@pytest.fixture
def transformers_stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[_TransformersStub]:
    stub = _TransformersStub()
    _TransformersStub.current = stub
    monkeypatch.setitem(sys.modules, "transformers", stub)
    yield stub
    _TransformersStub.current = None


@pytest.fixture
def impl_loader(transformers_stub: _TransformersStub, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[], ModuleType]]:
    """Import the heavy impl fresh against the stub transformers."""

    def _load() -> ModuleType:
        sys.modules.pop(_IMPL_MODULE, None)
        module = importlib.import_module(_IMPL_MODULE)
        return module

    yield _load
    sys.modules.pop(_IMPL_MODULE, None)


# ── Fake engine: scripted two-step responses ─────────────────────────────────


class _FakeEngine:
    """Records generate() calls; answers from a scripted response list."""

    def __init__(self, responses: list[object], error_at: int | None = None) -> None:
        self.responses = list(responses)
        self.error_at = error_at
        self.calls: list[dict[str, object]] = []

    def generate(self, images: list[Image.Image], prompts: list[str], max_new_tokens: int) -> list[str]:
        index = len(self.calls)
        self.calls.append({"images": len(images), "prompts": list(prompts), "max_new_tokens": max_new_tokens})
        if self.error_at is not None and index == self.error_at:
            raise RuntimeError("boom")
        response = self.responses.pop(0) if self.responses else ""
        return list(response) if isinstance(response, (list, tuple)) else [str(response)] * len(prompts)


def _tag(kind: str, x0: int, y0: int, x1: int, y1: int, tail: str = "") -> str:
    return f"<|box_start|>{x0} {y0} {x1} {y1}<|box_end|><|ref_start|>{kind}<|ref_end|>{tail}"


_RICH_LAYOUT = "".join(
    (
        _tag("title", 50, 50, 950, 120),
        _tag("text", 50, 150, 950, 250),
        _tag("text", 50, 260, 950, 300, " txt_contd_tgt "),
        _tag("list_item", 50, 320, 950, 360),
        _tag("table", 50, 380, 950, 560),
        _tag("equation", 50, 580, 950, 620),
        _tag("image", 50, 640, 400, 840),
        _tag("image_caption", 50, 850, 400, 880),
        _tag("header", 50, 940, 950, 970),
        _tag("footer", 50, 975, 950, 995),
        _tag("page_number", 900, 975, 950, 995),  # page furniture: dropped
    )
)

_RICH_RECOGNITION = [
    "Big Title",
    "First paragraph.",
    "continued",
    "item one",
    # Realistic OTSL: one cell token per grid position, no <ecel> terminators
    # between filled cells (that slot semantics is pinned by the _content tests).
    "<fcel>H1<fcel>H2<nl><fcel>a<fcel>b",
    "E = mc^2",
    "Figure 1",
    "Journal",
    "12",
    "1",  # page_number is recognized but dropped at mapping — still one response
]

_ONE_PAGE: list[tuple[str, tuple[float, float]]] = [("", (612.0, 792.0))]


@pytest.fixture
def backend_factory(
    impl_loader: Callable[[], ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., tuple[ModuleType, DocumentBackend, _FakeEngine]]:
    """A MineruBackend over scripted pages and a scripted engine."""

    def _make(
        pages: list[tuple[str, tuple[float, float]]] | None = None,
        responses: list[object] | None = None,
        options: dict[str, str | int | float | bool] | None = None,
        error_at: int | None = None,
    ) -> tuple[ModuleType, DocumentBackend, _FakeEngine]:
        impl = impl_loader()
        monkeypatch.setattr(impl, "pdfium", _fake_pdfium(pages if pages is not None else _ONE_PAGE))
        monkeypatch.setattr(impl, "_raster", lambda payload, number: _png())
        engine = _FakeEngine(responses or [], error_at=error_at)
        monkeypatch.setattr(impl, "_load_engine", lambda config: engine)
        merged: dict[str, str | int | float | bool] = {"effort": "medium"}
        merged.update(options or {})
        backend = impl.MineruBackend(impl.BackendConfig(name="mineru", options=merged))
        return impl, backend, engine

    return _make


def _request(
    *,
    page_range: PageRange | None = None,
    timeout_s: float | None = None,
    cancellation: Callable[[], bool] | None = None,
    max_output_chars: int | None = None,
    max_context_tokens: int | None = None,
) -> ConversionRequest:
    return ConversionRequest(
        source=_PDF_SOURCE,
        page_range=page_range,
        timeout_s=timeout_s,
        cancellation=cancellation,
        max_output_chars=max_output_chars,
        max_context_tokens=max_context_tokens,
    )


# ── Light factory: descriptor, asset, eligibility, boundary ───────────────────


def test_descriptor_declares_the_verified_surface() -> None:
    capabilities = mineru_module.DESCRIPTOR.capabilities
    assert capabilities.supported_formats == ["application/pdf"]
    assert capabilities.gpu_requirement == 0.5  # GPU_OPTIONAL: a slow answer beats no answer
    assert capabilities.estimated_vram_gb == 4.0
    assert capabilities.supports_page_ranges is True
    assert capabilities.optional_dependency_group == "mineru"
    assert capabilities.convert_extra == "mineru"


def test_asset_is_pinned_and_apache() -> None:
    asset = mineru_module.MINERU_ASSET
    assert asset.model_id == "opendatalab/MinerU2.5-Pro-2605-1.2B"
    assert asset.model_revision == "08aaea840498d49ce16247b6263196cf02814885"
    assert asset.model_license == "apache-2.0"
    assert asset.code_license == "apache-2.0"
    assert asset.asset_license == "apache-2.0"
    assert len(asset.file_pins) == 11  # parsecraft owns the download
    assert asset.size_bytes == 2_328_011_610
    assert any("mineru-vl-utils" in url for url in asset.source_urls)


def test_hard_eligible_without_a_gpu() -> None:
    constraints = RoutingConstraints(
        formats={"application/pdf"},
        installed_extras={"mineru"},
        gpu_usable=False,
        vram_budget_gb=0.0,
        offline=False,  # isolate the GPU rule; the offline rule has its own test
    )
    assert is_hard_eligible(mineru_module.DESCRIPTOR, constraints) is True


def test_offline_eligibility_follows_the_pinned_cache() -> None:
    # Offline defaults to a fail-safe True: pinned weights that are not verified
    # present in the managed cache make the backend ineligible — but once the
    # exact model@revision is cached, no download is needed and it re-enters.
    asset = mineru_module.MINERU_ASSET
    marker = f"{asset.model_id}@{asset.model_revision}"
    denied = RoutingConstraints(
        formats={"application/pdf"},
        installed_extras={"mineru"},
        offline=True,
        cached_assets=frozenset(),
    )
    assert is_hard_eligible(mineru_module.DESCRIPTOR, denied) is False
    granted = RoutingConstraints(
        formats={"application/pdf"},
        installed_extras={"mineru"},
        offline=True,
        cached_assets=frozenset({marker}),
    )
    assert is_hard_eligible(mineru_module.DESCRIPTOR, granted) is True


def test_factory_names_the_missing_dependency(impl_loader: Callable[[], ModuleType], monkeypatch: pytest.MonkeyPatch) -> None:
    impl_loader()  # prove the impl imports against the stub
    monkeypatch.setitem(sys.modules, "transformers", None)  # halt the heavy import
    sys.modules.pop(_IMPL_MODULE, None)
    with pytest.raises(DependencyUnavailableError) as details:
        mineru_module.factory(BackendConfig(name="mineru"))
    assert "transformers" in str(details.value)
    assert "mineru" in str(details.value)


def test_load_impl_returns_a_module_that_creates(impl_loader: Callable[[], ModuleType]) -> None:
    impl_loader()  # the transformers stubs must be in place for the heavy import
    module = mineru_module._load_impl()
    assert module is importlib.import_module(_IMPL_MODULE)
    assert callable(module.create)


def test_factory_rejects_an_impl_without_create(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_impl = ModuleType(_IMPL_MODULE)  # no create()
    monkeypatch.setitem(sys.modules, _IMPL_MODULE, fake_impl)
    with pytest.raises(BackendError):
        mineru_module.factory(BackendConfig(name="mineru"))


# ── Engine loading and generation ─────────────────────────────────────────────


class _AssetManagerSpy:
    instances: list[_AssetManagerSpy] = []

    def __init__(self, *, offline: bool = False, downloader: object = None) -> None:
        self.offline = offline
        self.downloader = downloader
        self.pins: list[object] = []
        type(self).instances.append(self)

    def ensure(self, pin: object) -> list[str]:
        self.pins.append(pin)
        filenames = getattr(pin, "filenames", [])
        return [f"fake-assets/{name}" for name in filenames]

    @staticmethod
    def revision_dir(model_id: str, revision: str) -> Path:
        return Path("fake-assets") / model_id.replace("/", "--") / revision[:12]


@pytest.fixture
def asset_spy(monkeypatch: pytest.MonkeyPatch) -> type[_AssetManagerSpy]:
    from parsecraft.backends.ocr import _common

    monkeypatch.setattr(_common, "AssetManager", _AssetManagerSpy)
    return _AssetManagerSpy


def test_load_engine_pins_and_loads(
    impl_loader: Callable[[], ModuleType],
    transformers_stub: _TransformersStub,
    asset_spy: type[_AssetManagerSpy],
) -> None:
    impl = impl_loader()
    engine = impl._load_engine(BackendConfig(name="mineru"))
    assert isinstance(engine, impl._Engine)
    # The pinned asset was ensured, and the managed revision dir was used.
    assert asset_spy.instances[-1].pins, "assets must be ensured through the manager"
    load = transformers_stub.model_loads[0]
    assert str(load["source"]).startswith("fake-assets")
    assert load["config"] is not None
    assert load["device_map"] == "auto"
    assert load["dtype"] == "auto"
    processor_load = transformers_stub.processor_loads[0]
    assert processor_load["fix_mistral_regex"] is True
    image_load = transformers_stub.image_processor_loads[0]
    assert image_load["backend"] == "torchvision"


def test_load_engine_hub_source_when_unpinned(
    impl_loader: Callable[[], ModuleType],
    transformers_stub: _TransformersStub,
    asset_spy: type[_AssetManagerSpy],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    impl = impl_loader()
    unpinned = mineru_module.MINERU_ASSET.model_copy(update={"file_pins": ()})
    monkeypatch.setattr(impl, "MINERU_ASSET", unpinned)
    impl._load_engine(BackendConfig(name="mineru"))
    load = transformers_stub.model_loads[0]
    assert load["source"] == unpinned.model_id
    assert load["revision"] == unpinned.model_revision  # hub path forwards the revision


def test_load_engine_wraps_load_failures(
    impl_loader: Callable[[], ModuleType], transformers_stub: _TransformersStub, asset_spy: type[_AssetManagerSpy]
) -> None:
    impl = impl_loader()
    transformers_stub.load_error = RuntimeError("disk on fire")
    with pytest.raises(BackendError, match="failed to load model"):
        impl._load_engine(BackendConfig(name="mineru"))


def test_load_engine_reraises_typed_errors_unwrapped(
    impl_loader: Callable[[], ModuleType], transformers_stub: _TransformersStub, asset_spy: type[_AssetManagerSpy]
) -> None:
    impl = impl_loader()
    transformers_stub.load_error = BackendError("typed already")
    with pytest.raises(BackendError, match="^typed already$"):
        impl._load_engine(BackendConfig(name="mineru"))


def test_tie_weights_sync_uses_text_config_fallback(
    impl_loader: Callable[[], ModuleType], transformers_stub: _TransformersStub, asset_spy: type[_AssetManagerSpy]
) -> None:
    impl = impl_loader()
    _TransformersStub.raw_config = {}  # raw dict silent about tying
    try:
        impl._load_engine(BackendConfig(name="mineru"))
        config = transformers_stub.configs[0]
        assert config.text_config.tie_word_embeddings is True  # fell back, then synced
        assert config.tie_word_embeddings is True
    finally:
        _TransformersStub.raw_config = {"tie_word_embeddings": True}


def test_engine_generate_slices_filters_and_decodes(impl_loader: Callable[[], ModuleType]) -> None:
    impl = impl_loader()

    class _DecodeProcessor:
        tokenizer = SimpleNamespace(pad_token_id=3)

        @staticmethod
        def apply_chat_template(conversation: object, **options: object) -> str:
            return "CHAT"

        def __call__(self, **kwargs: object) -> dict[str, object]:
            return {"input_ids": _FakeTensor([[1, 2, 3], [4, 5, 6]])}

        @staticmethod
        def batch_decode(sequences: list[list[int]], **options: object) -> list[str]:
            return [" ".join(str(token) for token in sequences[0])]

    class _GeneratingModel:
        device = "cpu"
        dtype = "float32"
        generation_config = None
        config = None
        kwargs: dict[str, object] = {}

        def generate(self, **kwargs: object) -> _FakeTensor:
            self.kwargs = kwargs
            return _FakeTensor([[1, 2, 3, 10, 11], [4, 5, 6, 10, 12]])

    model = _GeneratingModel()
    engine = impl._Engine(model, _DecodeProcessor(), frozenset({10, 3}))
    results = engine.generate([Image.new("RGB", (8, 8)) for _ in range(2)], ["p1", "p2"], 99)
    assert results == ["11", "12"]  # input prefix sliced off, skip ids filtered
    assert model.kwargs["do_sample"] is False
    assert model.kwargs["no_repeat_ngram_size"] == 100
    assert model.kwargs["max_new_tokens"] == 99


def test_skip_token_ids_collects_every_holder(impl_loader: Callable[[], ModuleType]) -> None:
    impl = impl_loader()
    model = SimpleNamespace(
        generation_config=SimpleNamespace(bos_token_id=0, eos_token_id=[1, 2], pad_token_id=None),
        config=SimpleNamespace(bos_token_id=None, eos_token_id=5, pad_token_id=None),
    )
    processor = SimpleNamespace(tokenizer=SimpleNamespace(bos_token_id=None, eos_token_id=None, pad_token_id=3))
    assert impl._skip_token_ids(model, processor) == frozenset({0, 1, 2, 5, 3})


# ── Options ───────────────────────────────────────────────────────────────────


def test_effort_default_and_validation(impl_loader: Callable[[], ModuleType]) -> None:
    impl = impl_loader()
    assert impl._effort(impl.BackendConfig(name="mineru")) is impl.MineruEffort.FLASH
    assert impl._effort(impl.BackendConfig(name="mineru", options={"effort": "high"})) is impl.MineruEffort.HIGH
    with pytest.raises(BackendError, match="unknown effort 'turbo'"):
        impl._effort(impl.BackendConfig(name="mineru", options={"effort": "turbo"}))


def test_image_analysis_coercion(impl_loader: Callable[[], ModuleType]) -> None:
    impl = impl_loader()
    for raw, expected in (("true", True), ("yes", True), ("on", True), ("no", False), ("false", False)):
        assert impl._image_analysis(impl.BackendConfig(name="mineru", options={"image_analysis": raw})) is expected
    with pytest.raises(BackendError, match="boolean"):
        impl._image_analysis(impl.BackendConfig(name="mineru", options={"image_analysis": "banana"}))


# ── analyze: cheap signals ────────────────────────────────────────────────────


def test_analyze_reports_per_page_signals(backend_factory: Callable[..., tuple[ModuleType, DocumentBackend, _FakeEngine]]) -> None:
    _, backend, _ = backend_factory(pages=[("alpha", (612.0, 792.0)), ("", (612.0, 792.0))])
    result = backend.analyze(_PDF_SOURCE)
    assert result.page_count == 2
    assert [signal.page_number for signal in result.signals] == [1, 2]
    assert [signal.has_native_text for signal in result.signals] == [True, False]
    assert result.signals[0].text_chars == len("alpha")
    assert result.signals[1].blank is True


def test_analyze_empty_document_falls_back_to_one_blank_signal(
    backend_factory: Callable[..., tuple[ModuleType, DocumentBackend, _FakeEngine]],
) -> None:
    _, backend, _ = backend_factory(pages=[])
    result = backend.analyze(_PDF_SOURCE)
    assert result.page_count == 1
    assert result.signals[0].blank is True


# ── convert: the two-step protocol ────────────────────────────────────────────


def test_convert_maps_blocks_to_typed_ir(
    backend_factory: Callable[..., tuple[ModuleType, DocumentBackend, _FakeEngine]],
) -> None:
    impl, backend, engine = backend_factory(responses=[_RICH_LAYOUT, _RICH_RECOGNITION])
    result = backend.convert(_request())
    assert [failure.code for failure in result.failures] == []
    page = result.pages[0]
    kinds = [chunk.kind.value for chunk in page.blocks]
    assert kinds == [
        "heading",
        "paragraph",  # merged continuation absorbed here
        "list",
        "table",
        "formula",
        "figure",
        "caption",
        "header",
        "footer",
    ]
    assert page.blocks[0].content == "Big Title"
    assert page.blocks[0].metadata["heading_level"] == "1"
    assert page.blocks[1].content == "First paragraph.\ncontinued"
    assert page.blocks[3].content == "H1 | H2\na | b"
    assert page.blocks[3].rows == (("H1", "H2"), ("a", "b"))
    assert page.blocks[4].content == "\\[ E = mc^2 \\]"
    assert [chunk.id for chunk in page.blocks][0] == "mineru-1-b0"
    # Figure crop becomes a content-addressed asset.
    assert len(page.assets) == 1
    asset = page.assets[0]
    assert asset.uri.startswith("data:image/png;base64,")
    assert len(asset.sha256 or "") == 64
    assert page.blocks[5].metadata["asset_id"] == asset.id
    # Two engine passes: layout, then recognition (page_number is recognized
    # too — its content is dropped at mapping, but the batch still covers it).
    assert [call["images"] for call in engine.calls] == [1, 10]


def test_convert_budget_from_request_beats_effort_dial(
    backend_factory: Callable[..., tuple[ModuleType, DocumentBackend, _FakeEngine]],
) -> None:
    _, backend, engine = backend_factory(responses=[_tag("text", 10, 10, 90, 90), "words"])
    result = backend.convert(_request(max_context_tokens=77))
    assert engine.calls[-1]["max_new_tokens"] == 77
    assert result.pages[0].blocks[0].content == "words"


def test_convert_effort_budgets_escalate(
    backend_factory: Callable[..., tuple[ModuleType, DocumentBackend, _FakeEngine]],
) -> None:
    impl, _, _ = backend_factory()
    assert impl.EFFORT_BUDGETS[impl.MineruEffort.FLASH] == 2048
    assert impl.EFFORT_BUDGETS[impl.MineruEffort.HIGH] == 4096
    assert impl.EFFORT_BUDGETS[impl.MineruEffort.XHIGH] == 8192


def test_convert_flash_uses_the_text_layer_without_the_model(
    backend_factory: Callable[..., tuple[ModuleType, DocumentBackend, _FakeEngine]],
) -> None:
    _, backend, engine = backend_factory(
        pages=[("para one\n\npara two", (612.0, 792.0))],
        responses=[],  # no engine answers scripted: none may be requested
        options={"effort": "flash"},
    )
    result = backend.convert(_request())
    assert engine.calls == []
    assert [chunk.content for chunk in result.pages[0].blocks] == ["para one", "para two"]
    assert all(chunk.kind.value == "paragraph" for chunk in result.pages[0].blocks)


def test_convert_flash_falls_back_to_the_vlm_on_textless_pages(
    backend_factory: Callable[..., tuple[ModuleType, DocumentBackend, _FakeEngine]],
) -> None:
    _, backend, engine = backend_factory(
        pages=[("", (612.0, 792.0))],
        responses=[_tag("text", 10, 10, 90, 90), "rescued text"],
        options={"effort": "flash"},
    )
    result = backend.convert(_request())
    assert len(engine.calls) == 2
    assert result.pages[0].blocks[0].content == "rescued text"


def test_medium_loads_the_engine_at_create(impl_loader: Callable[[], ModuleType], monkeypatch: pytest.MonkeyPatch) -> None:
    impl = impl_loader()
    loads: list[int] = []
    monkeypatch.setattr(impl, "_load_engine", lambda config: loads.append(1) or object())
    impl.MineruBackend(impl.BackendConfig(name="mineru", options={"effort": "medium"}))
    assert loads == [1]
    impl.MineruBackend(impl.BackendConfig(name="mineru", options={"effort": "flash"}))
    assert loads == [1]  # flash defers the weights


# ── convert: bounds and failures ──────────────────────────────────────────────


def test_convert_page_range_out_of_bounds(
    backend_factory: Callable[..., tuple[ModuleType, DocumentBackend, _FakeEngine]],
) -> None:
    _, backend, _ = backend_factory(pages=_ONE_PAGE * 2)
    result = backend.convert(_request(page_range=PageRange(start=5, end=6)))
    assert result.pages == []
    assert result.failures[0].code is FailureCode.INVALID_INPUT
    assert "outside the 2-page document" in result.failures[0].detail


def test_convert_page_range_natively_honored(
    backend_factory: Callable[..., tuple[ModuleType, DocumentBackend, _FakeEngine]],
) -> None:
    _, backend, _ = backend_factory(pages=[("alpha", (612.0, 792.0)), ("beta", (612.0, 792.0))])
    result = backend.convert(_request(page_range=PageRange(start=2, end=2), timeout_s=30.0, max_output_chars=10_000))
    assert [page.page_number for page in result.pages] == [2]


def test_convert_cancelled_before_start(
    backend_factory: Callable[..., tuple[ModuleType, DocumentBackend, _FakeEngine]],
) -> None:
    _, backend, _ = backend_factory()
    result = backend.convert(_request(cancellation=lambda: True))
    assert result.pages == []
    assert result.failures[0].code is FailureCode.CANCELLED


def test_convert_cancelled_between_pages(
    backend_factory: Callable[..., tuple[ModuleType, DocumentBackend, _FakeEngine]],
) -> None:
    _, backend, _ = backend_factory(pages=[("alpha", (612.0, 792.0)), ("beta", (612.0, 792.0))])
    # Call 1 = pre-conversion, call 2 = before page 1, call 3 = before page 2.
    calls: list[int] = []

    def cancellation() -> bool:
        calls.append(1)
        return len(calls) >= 3

    result = backend.convert(_request(cancellation=cancellation))
    assert [page.page_number for page in result.pages] == [1]
    assert result.failures[0].code is FailureCode.CANCELLED
    assert "before page 2" in result.failures[0].detail


def test_convert_timeout_before_first_page(
    backend_factory: Callable[..., tuple[ModuleType, DocumentBackend, _FakeEngine]],
    impl_loader: Callable[[], ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    impl, backend, _ = backend_factory()
    tick = _Ticker()
    monkeypatch.setattr(impl, "monotonic", tick)
    result = backend.convert(_request(timeout_s=1.0))
    assert result.pages == []
    assert result.failures[0].code is FailureCode.TIMEOUT
    assert "before page 1" in result.failures[0].detail


class _Ticker:
    """monotonic() stand-in advancing 100s per call — deadlines trip on cue."""

    def __init__(self, start_at: int = 0) -> None:
        self._calls = start_at

    def __call__(self) -> float:
        self._calls += 1
        return self._calls * 100.0


def test_convert_timeout_during_page(backend_factory: Callable[..., tuple[ModuleType, DocumentBackend, _FakeEngine]], monkeypatch: pytest.MonkeyPatch) -> None:
    impl, backend, _ = backend_factory(responses=[_tag("text", 10, 10, 90, 90), "words"])
    monkeypatch.setattr(impl, "monotonic", _Ticker(start_at=-1))  # started=0, checks at 100/200
    result = backend.convert(_request(timeout_s=150.0))
    assert result.failures[0].code is FailureCode.TIMEOUT
    assert "during page 1" in result.failures[0].detail
    assert result.pages == []


def test_convert_per_page_error_does_not_sink_the_run(
    backend_factory: Callable[..., tuple[ModuleType, DocumentBackend, _FakeEngine]],
) -> None:
    _, backend, _ = backend_factory(
        pages=[("", (612.0, 792.0)), ("", (612.0, 792.0))],
        responses=[_tag("text", 10, 10, 90, 90), _tag("text", 10, 10, 90, 90), "words"],
        error_at=1,  # recognition of page 1 explodes; page 2 still converts
    )
    result = backend.convert(_request(timeout_s=30.0, max_output_chars=10_000))
    assert [page.page_number for page in result.pages] == [1, 2]
    assert result.pages[0].blocks == []  # failed page lands empty
    assert [chunk.content for chunk in result.pages[1].blocks] == ["words"]
    assert result.failures[0].code is FailureCode.BACKEND_ERROR
    assert "page 1: RuntimeError: boom" in result.failures[0].detail


def test_convert_output_budget_stops_before_overflow(
    backend_factory: Callable[..., tuple[ModuleType, DocumentBackend, _FakeEngine]],
) -> None:
    _, backend, _ = backend_factory(
        pages=[("", (612.0, 792.0)), ("", (612.0, 792.0))],
        responses=[_tag("text", 10, 10, 90, 90), ["words words words"]],
    )
    result = backend.convert(_request(timeout_s=30.0, max_output_chars=3))
    assert result.pages == []
    assert result.failures[0].code is FailureCode.BUDGET_EXCEEDED
    assert "3-character" in result.failures[0].detail


def test_convert_failure_reference_names_the_model(
    backend_factory: Callable[..., tuple[ModuleType, DocumentBackend, _FakeEngine]],
) -> None:
    _, backend, _ = backend_factory()
    result = backend.convert(_request(cancellation=lambda: True))
    failure = result.failures[0]
    assert failure.pass_kind is PassKind.NATIVE
    assert failure.backend == "mineru"
    assert result.backend.model_id == "opendatalab/MinerU2.5-Pro-2605-1.2B"


# ── Helpers ───────────────────────────────────────────────────────────────────


def test_window_rules(impl_loader: Callable[[], ModuleType]) -> None:
    impl = impl_loader()
    assert impl._window(None, 3) == range(1, 4)
    assert impl._window(PageRange(start=2, end=9), 3) == range(2, 4)  # clipped high
    assert impl._window(PageRange(start=1, end=1), 3) == range(1, 2)
    assert impl._window(PageRange(start=5, end=6), 2) is None
    assert impl._window(PageRange(start=2, end=2), 1) is None  # start past the end


def test_source_bytes_prefers_content_then_path(impl_loader: Callable[[], ModuleType], tmp_path: Path) -> None:
    impl = impl_loader()
    assert impl.source_bytes(_PDF_SOURCE) == _PDF_BYTES
    document = tmp_path / "doc.pdf"
    document.write_bytes(b"on disk")
    assert impl.source_bytes(SourceDocument(uri=document.as_uri(), media_type=None, content=None)) == b"on disk"
    missing = tmp_path / "missing.pdf"
    with pytest.raises(BackendError, match="cannot read source"):
        impl.source_bytes(SourceDocument(uri=missing.as_uri(), media_type=None, content=None))


def test_create_is_the_heavy_import_boundary(impl_loader: Callable[[], ModuleType]) -> None:
    impl = impl_loader()
    backend = impl.create(impl.BackendConfig(name="mineru"))
    assert isinstance(backend, impl.MineruBackend)


def test_signal_falls_back_for_out_of_range_pages(impl_loader: Callable[[], ModuleType]) -> None:
    impl = impl_loader()
    fallback = impl._signal([], 99)
    assert fallback.page_number == 99
    assert fallback.blank is True
    assert fallback.has_native_text is False


def test_raster_goes_through_the_shared_surface(impl_loader: Callable[[], ModuleType], monkeypatch: pytest.MonkeyPatch) -> None:
    impl = impl_loader()
    calls: list[tuple[SourceDocument, int]] = []

    def fake_rasterize(source: SourceDocument, page_number: int) -> bytes:
        calls.append((source, page_number))
        return b"png"

    monkeypatch.setattr(impl, "rasterize_page", fake_rasterize)
    assert impl._raster(b"%PDF", 3) == b"png"
    source, page_number = calls[0]
    assert source.content == b"%PDF"
    assert source.uri == "inline://page/3"
    assert page_number == 3


def test_block_bbox_returns_none_for_inverted_blocks(impl_loader: Callable[[], ModuleType]) -> None:
    impl = impl_loader()
    block = impl.LayoutBlock(type="text", x0=0.9, y0=0.1, x1=0.1, y1=0.9, angle=0, merge_prev=False)
    assert impl._block_bbox(block, 612.0, 792.0) is None


def test_crop_block_rotates_the_region(impl_loader: Callable[[], ModuleType]) -> None:
    impl = impl_loader()
    page = Image.new("RGB", (100, 100), (255, 255, 255))
    block = impl.LayoutBlock(type="image", x0=0.1, y0=0.1, x1=0.5, y1=0.5, angle=90, merge_prev=False)
    crop = impl._crop_block(page, block)
    assert crop.size == (40, 40)  # square region stays square, just turned


# ── Recognition edge cases ─────────────────────────────────────────────────────


def test_non_text_recognition_drops_the_block(
    backend_factory: Callable[..., tuple[ModuleType, DocumentBackend, _FakeEngine]],
) -> None:
    _, backend, _ = backend_factory(responses=[_tag("text", 10, 10, 90, 90), "[Non-Text]"])
    result = backend.convert(_request())
    assert result.pages[0].blocks == []


def test_image_analysis_classifies_recognized_images(
    backend_factory: Callable[..., tuple[ModuleType, DocumentBackend, _FakeEngine]],
) -> None:
    layout = _tag("image", 10, 10, 90, 90) + _tag("chart", 10, 50, 90, 90) + _tag("image", 10, 60, 90, 95)
    responses = [
        layout,
        [
            "<|class_start|>pure_table<|class_end|><|content_start|>| A | B |\n|---|---|\n| 1 | 2 |<|content_end|>",
            "<|class_start|>pure_formula<|class_end|><|content_start|>a+b<|content_end|>",
            "<|class_start|>natural_image<|class_end|><|caption_start|>A photo<|caption_end|>",
        ],
    ]
    _, backend, engine = backend_factory(responses=responses, options={"image_analysis": "true"})
    result = backend.convert(_request())
    blocks = result.pages[0].blocks
    assert [block.kind.value for block in blocks] == ["table", "formula", "figure"]
    assert blocks[0].rows == (("A", "B"), ("1", "2"))
    assert blocks[1].content == "\\[ a+b \\]"
    assert blocks[2].content == "A photo"
    # With analysis on, every figure is recognized.
    assert engine.calls[-1]["images"] == 3


def test_image_analysis_without_caption_drops_the_figure(
    backend_factory: Callable[..., tuple[ModuleType, DocumentBackend, _FakeEngine]],
) -> None:
    response = "<|class_start|>natural_image<|class_end|>"  # no caption, no content
    _, backend, _ = backend_factory(
        responses=[_tag("image", 10, 10, 90, 90), response],
        options={"image_analysis": "true"},
    )
    result = backend.convert(_request())
    assert result.pages[0].blocks == []
