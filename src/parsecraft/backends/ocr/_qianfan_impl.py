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
    convert_pages,
    load_transformers_pipeline,
    load_vllm,
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
    QIANFAN_MODEL_ID,
    QIANFAN_NAME,
    QIANFAN_REVISION,
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

#: Adapter prompt: Layout-as-Thought — plan the layout, then transcribe regions.
PROMPT = "Layout-as-Thought: first outline the regions of this page (headers, columns, tables, figures), then transcribe each region fully in reading order."


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
    if runtime_choice(config) == "vllm":
        vllm_module = load_vllm()
        engine = vllm_module.LLM(model=QIANFAN_MODEL_ID, revision=QIANFAN_REVISION)
        transcriber = vllm_transcriber(
            engine,
            module=vllm_module,
            prompt=PROMPT,
            image_extra=QIANFAN_EXTRA,
        )
    else:
        pipe = load_transformers_pipeline(
            pipeline,
            model_id=QIANFAN_MODEL_ID,
            model_revision=QIANFAN_REVISION,
        )
        transcriber = transformers_transcriber(pipe, prompt=PROMPT)
    return _QianfanBackend(config, transcriber)
