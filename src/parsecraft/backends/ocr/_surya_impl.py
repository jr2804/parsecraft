"""Heavy SuryaOCR 2.5 implementation — Transformers imported at top level by design.

Loaded only from the light factory at instantiation (``importlib``-based, never
an inline import). Model pin + license: ``_models.SURYA_ASSET`` (pinned against
the HF revision API 2026-10-30; OpenRAIL-M weights, first consumer of
``requires_user_acceptance``). Runtime is pluggable via ``options["runtime"]``:
``transformers`` (default) or ``vllm`` (extra ``vllm``).
"""

from __future__ import annotations

from transformers import AutoProcessor, pipeline  # ty: ignore[unresolved-import] — extra not installed in dev/CI; heavy by contract

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
    SURYA,
    SURYA_ASSET,
    SURYA_CAPABILITIES,
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

#: Card-verified prompt: the high-accuracy page prompt from the upstream training
#: contract (``surya/inference/prompts.py`` in datalab-to/surya, Apache-2.0 —
#: "The exact wording is the model's training-time contract — do not paraphrase
#: without retraining"). Quoted verbatim: page in, HTML blocks with normalized
#: bboxes out. ``convert_pages`` carries the HTML through as page text; the
#: ``data-bbox`` spans are phase-2 orchestration material (pc-81q), not parsed here.
PROMPT = "OCR this image to HTML. Each block is a div with data-label and data-bbox (x0 y0 x1 y1, normalized 0-1000)."


class _SuryaBackend:
    """Instantiated SuryaOCR 2.5 backend: bound transcriber + light orchestration."""

    name = SURYA.name
    capabilities: BackendCapabilities = SURYA_CAPABILITIES

    def __init__(self, config: BackendConfig, transcriber: Transcriber) -> None:
        self._config = config
        self._transcribe = transcriber

    def convert(self, request: ConversionRequest) -> BackendResult:
        def _infer(number: int, inner: ConversionRequest) -> str:
            image = rasterize_page(inner.source, number)
            return self._transcribe(image, inner.max_context_tokens)

        return convert_pages(
            backend_name=SURYA.name,
            backend_version=OCR_BACKEND_VERSION,
            asset=SURYA_ASSET,
            source=request.source,
            request=request,
            infer_page=_infer,
        )

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        return analyze_source(source)


def create(config: BackendConfig) -> DocumentBackend:
    """Build the backend — the sanctioned heavy-import boundary."""
    source, revision = model_source_and_revision(SURYA_ASSET, config)
    if runtime_choice(config) == "vllm":
        vllm_module = load_vllm()
        engine_kwargs: dict[str, object] = {"model": source}
        if revision is not None:
            engine_kwargs["revision"] = revision
        engine = vllm_module.LLM(**engine_kwargs)
        templated = chat_prompt(engine.get_tokenizer(), user_text=PROMPT, enable_thinking=False)
        transcriber = vllm_transcriber(
            engine,
            module=vllm_module,
            prompt=templated,
            image_extra=SURYA.extra,
        )
    else:
        pipe = load_transformers_pipeline(
            pipeline,
            processor_loader=AutoProcessor.from_pretrained,
            model_source=source,
            model_revision=revision,
            require_gpu=True,  # GPU_REQUIRED capability: never a silent CPU fallback
        )
        templated = chat_prompt(pipe.tokenizer, user_text=PROMPT, enable_thinking=False)
        transcriber = transformers_transcriber(pipe, prompt=templated, image_extra=SURYA.extra)
    return _SuryaBackend(config, transcriber)
