"""Heavy TeleOCR implementation — Transformers imported at top level by design.

Loaded only from the light factory at instantiation (``importlib``-based, never
an inline import). Model pin + license: ``_models.TELE_ASSET`` (verified against
the HF API 2026-09-27). Runtime is pluggable via ``options["runtime"]``:
``transformers`` (default) or ``vllm`` (extra ``vllm``).
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
    TELE_ASSET,
    TELE_CAPABILITIES,
    TELE_EXTRA,
    TELE_MODEL_ID,
    TELE_NAME,
    TELE_REVISION,
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

#: Adapter prompt: verbatim page transcription in layout order.
PROMPT = "OCR this document page exactly as laid out, top to bottom in reading order. Do not summarise or reorder; keep original line and column structure."


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
    if runtime_choice(config) == "vllm":
        vllm_module = load_vllm()
        engine = vllm_module.LLM(model=TELE_MODEL_ID, revision=TELE_REVISION)
        transcriber = vllm_transcriber(
            engine,
            module=vllm_module,
            prompt=PROMPT,
            image_extra=TELE_EXTRA,
        )
    else:
        pipe = load_transformers_pipeline(
            pipeline,
            model_id=TELE_MODEL_ID,
            model_revision=TELE_REVISION,
        )
        transcriber = transformers_transcriber(pipe, prompt=PROMPT)
    return _TeleBackend(config, transcriber)
