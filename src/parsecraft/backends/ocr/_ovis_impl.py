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
    OVIS_ASSET,
    OVIS_CAPABILITIES,
    OVIS_EXTRA,
    OVIS_NAME,
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

#: Card-verified prompt (OvisOCR2 README): Markdown extraction with bbox image tags.
PROMPT = (
    "\nExtract all readable content from the image in natural human reading order and output the result "
    "as a single Markdown document. For charts or images, represent them using an HTML image tag: "
    '<img src="images/bbox_{left}_{top}_{right}_{bottom}.jpg" />, where left, top, right, bottom are bounding '
    "box coordinates scaled to [0, 1000). Format formulas as LaTeX. Format tables as HTML: <table>...</table>. "
    "Transcribe all other text as standard Markdown. Preserve the original text without translation or paraphrasing."
)


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
    source, revision = model_source_and_revision(OVIS_ASSET, config)
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
            image_extra=OVIS_EXTRA,
        )
    else:
        pipe = load_transformers_pipeline(
            pipeline,
            model_source=source,
            model_revision=revision,
        )
        templated = chat_prompt(pipe.tokenizer, user_text=PROMPT, enable_thinking=False)
        transcriber = transformers_transcriber(pipe, prompt=templated, image_extra=OVIS_EXTRA)
    return _OvisBackend(config, transcriber)
