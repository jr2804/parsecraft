"""Heavy Qianfan-OCR implementation — Transformers imported at top level by design.

Loaded only from the light factory at instantiation (``importlib``-based, never
an inline import). Model pin + license: ``_models.QIANFAN_ASSET`` (verified
against the HF API 2026-09-27). Prompt-controlled extraction (Layout-as-
Thought): the prompt carries the layout plan, the model follows it. Runtime is
pluggable via ``options["runtime"]``: ``transformers`` (default) or ``vllm``.
"""

from __future__ import annotations

from transformers import pipeline  # ty: ignore[unresolved-import] — extra not installed in dev/CI; heavy by contract

from parsecraft.backends.ocr._common import (
    Transcriber,
    analyze_source,
    chat_prompt,
    convert_pages,
    load_transformers_pipeline,
    load_vllm,
    model_source_and_revision,
    rasterize_page,
    runtime_choice,
    transformers_transcriber,
    vllm_transcriber,
)
from parsecraft.backends.ocr._models import (
    OCR_BACKEND_VERSION,
    QIANFAN_ASSET,
    QIANFAN_CAPABILITIES,
    QIANFAN_EXTRA,
    QIANFAN_NAME,
)
from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendCapabilities,
    BackendConfig,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
    SourceDocument,
)

#: Card-verified prompt (Qianfan-OCR README).
PROMPT = "Parse this document to Markdown."


class _QianfanBackend:
    """Instantiated Qianfan-OCR backend: bound transcriber + light orchestration."""

    name = QIANFAN_NAME
    capabilities: BackendCapabilities = QIANFAN_CAPABILITIES

    def __init__(self, config: BackendConfig, transcriber: Transcriber) -> None:
        self._config = config
        self._transcribe = transcriber

    def convert(self, request: ConversionRequest) -> BackendResult:
        def _infer(number: int, inner: ConversionRequest) -> str:
            image = rasterize_page(inner.source, number)
            return self._transcribe(image, inner.max_context_tokens)

        return convert_pages(
            backend_name=QIANFAN_NAME,
            backend_version=OCR_BACKEND_VERSION,
            asset=QIANFAN_ASSET,
            source=request.source,
            request=request,
            infer_page=_infer,
        )

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        return analyze_source(source)


def create(config: BackendConfig) -> DocumentBackend:
    """Build the backend — the sanctioned heavy-import boundary."""
    source, revision = model_source_and_revision(QIANFAN_ASSET, config)
    if runtime_choice(config) == "vllm":
        vllm_module = load_vllm()
        engine_kwargs: dict[str, object] = {"model": source}
        if revision is not None:
            engine_kwargs["revision"] = revision
        engine = vllm_module.LLM(**engine_kwargs)
        templated = chat_prompt(engine.get_tokenizer(), user_text=PROMPT)
        transcriber = vllm_transcriber(
            engine,
            module=vllm_module,
            prompt=templated,
            image_extra=QIANFAN_EXTRA,
        )
    else:
        pipe = load_transformers_pipeline(
            pipeline,
            model_source=source,
            model_revision=revision,
            require_gpu=True,  # GPU_REQUIRED capability: never a silent CPU fallback
        )
        templated = chat_prompt(pipe.tokenizer, user_text=PROMPT)
        transcriber = transformers_transcriber(pipe, prompt=templated, image_extra=QIANFAN_EXTRA)
    return _QianfanBackend(config, transcriber)
