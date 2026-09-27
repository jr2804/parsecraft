"""Heavy OvisOCR2 implementation — Transformers imported at top level by design.

Loaded only from the light factory at instantiation (``importlib``-based, never
an inline import). Model pin + license: ``_models.OVIS_ASSET`` (verified against
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
    OVIS_ASSET,
    OVIS_CAPABILITIES,
    OVIS_EXTRA,
    OVIS_MODEL_ID,
    OVIS_NAME,
    OVIS_REVISION,
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

#: Adapter prompt: full-page transcription in reading order, tables as Markdown.
PROMPT = "Transcribe every text region of this document page in reading order. Preserve the layout structure; render tables as Markdown."


class _OvisBackend:
    """Instantiated OvisOCR2 backend: bound transcriber + light orchestration."""

    name = OVIS_NAME
    capabilities: BackendCapabilities = OVIS_CAPABILITIES

    def __init__(self, config: BackendConfig, transcriber: Transcriber) -> None:
        self._config = config
        self._transcribe = transcriber

    def convert(self, request: ConversionRequest) -> BackendResult:
        def _infer(number: int, inner: ConversionRequest) -> str:
            image = rasterize_page(inner.source, number)
            return self._transcribe(image, inner.max_context_tokens)

        return convert_pages(
            backend_name=OVIS_NAME,
            backend_version=OCR_BACKEND_VERSION,
            asset=OVIS_ASSET,
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
        engine = vllm_module.LLM(model=OVIS_MODEL_ID, revision=OVIS_REVISION)
        transcriber = vllm_transcriber(
            engine,
            module=vllm_module,
            prompt=PROMPT,
            image_extra=OVIS_EXTRA,
        )
    else:
        pipe = load_transformers_pipeline(
            pipeline,
            model_id=OVIS_MODEL_ID,
            model_revision=OVIS_REVISION,
        )
        transcriber = transformers_transcriber(pipe, prompt=PROMPT)
    return _OvisBackend(config, transcriber)
