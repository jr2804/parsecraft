"""Heavy TeleOCR implementation — Transformers imported at top level by design.

Loaded only from the light factory at instantiation (``importlib``-based, never
an inline import). Model pin + license: ``_models.TELE_ASSET`` (verified against
the HF API 2026-09-27). Runtime is pluggable via ``options["runtime"]``:
``transformers`` (default) or ``vllm`` (extra ``vllm``).
"""

from __future__ import annotations

from transformers import AutoProcessor, pipeline  # ty: ignore[unresolved-import] — extra not installed in dev/CI; heavy by contract

from parsecraft.backends.errors import BackendError
from parsecraft.backends.ocr._common import (
    Transcriber,
    analyze_source,
    chat_prompt,
    convert_pages,
    load_vllm,
    model_source_and_revision,
    rasterize_page,
    require_cuda_device,
    require_transformers,
    runtime_choice,
    transformers_transcriber,
    vllm_transcriber,
)
from parsecraft.backends.ocr._models import (
    OCR_BACKEND_VERSION,
    TELE_ASSET,
    TELE_CAPABILITIES,
    TELE_EXTRA,
    TELE_NAME,
)
from parsecraft.backends.ocr._vendored.naviocr.modeling_naviocr import Qwen2_5_VLForConditionalGeneration
from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendCapabilities,
    BackendConfig,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
    SourceDocument,
)

#: Card-verified prompt (TeleOCR README): general text extraction.
PROMPT = "Please output the text content from the image."
#: Card-verified system message (TeleOCR README `infer`).
SYSTEM = "You are a helpful assistant."


class _TeleBackend:
    """Instantiated TeleOCR backend: bound transcriber + light orchestration."""

    name = TELE_NAME
    capabilities: BackendCapabilities = TELE_CAPABILITIES

    def __init__(self, config: BackendConfig, transcriber: Transcriber) -> None:
        self._config = config
        self._transcribe = transcriber

    def convert(self, request: ConversionRequest) -> BackendResult:
        def _infer(number: int, inner: ConversionRequest) -> str:
            image = rasterize_page(inner.source, number)
            return self._transcribe(image, inner.max_context_tokens)

        return convert_pages(
            backend_name=TELE_NAME,
            backend_version=OCR_BACKEND_VERSION,
            asset=TELE_ASSET,
            source=request.source,
            request=request,
            infer_page=_infer,
        )

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        return analyze_source(source)


def create(config: BackendConfig) -> DocumentBackend:
    """Build the backend — the sanctioned heavy-import boundary."""
    source, revision = model_source_and_revision(TELE_ASSET, config)
    if runtime_choice(config) == "vllm":
        vllm_module = load_vllm()
        engine_kwargs: dict[str, object] = {"model": source}
        if revision is not None:
            engine_kwargs["revision"] = revision
        engine = vllm_module.LLM(**engine_kwargs)
        templated = chat_prompt(engine.get_tokenizer(), user_text=PROMPT, system=SYSTEM)
        transcriber = vllm_transcriber(
            engine,
            module=vllm_module,
            prompt=templated,
            image_extra=TELE_EXTRA,
        )
    else:
        # Vendored modeling (pc-4u7.36): explicit class, no trust_remote_code.
        # Stock qwen2_5_vl cannot load these weights (head_dim=128) and the
        # repo's remote code is 4.x-only — see _vendored/README.md for the
        # port record. vLLM's own loader keeps using the repo as before.
        model_kwargs: dict[str, object] = {"device_map": "auto", "dtype": "auto"}
        processor_kwargs: dict[str, object] = {}
        if revision is not None:
            model_kwargs["revision"] = revision
            processor_kwargs["revision"] = revision
        where = f" at revision {revision[:12]}" if revision is not None else ""
        require_transformers()
        try:
            model = Qwen2_5_VLForConditionalGeneration.from_pretrained(source, **model_kwargs)
            processor = AutoProcessor.from_pretrained(source, **processor_kwargs)
            pipe = pipeline(task="image-text-to-text", model=model, processor=processor)
            require_cuda_device(pipe, model_source=source)  # GPU_REQUIRED capability
        except Exception as exc:  # model/stack load boundary — typed, never raw
            msg = f"failed to load model {source!r}{where}: {type(exc).__name__}: {exc}"
            raise BackendError(msg) from exc
        templated = chat_prompt(pipe.tokenizer, user_text=PROMPT, system=SYSTEM)
        transcriber = transformers_transcriber(pipe, prompt=templated, image_extra=TELE_EXTRA)
    return _TeleBackend(config, transcriber)
