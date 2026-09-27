"""Heavy Unlimited-OCR implementation — Transformers imported at top level by design.

Loaded only from the light factory at instantiation (``importlib``-based, never
an inline import). Model pin + license: ``_models.UNLIMITED_ASSET`` (verified
against the HF API 2026-09-27).

Long-horizon extraction: the plan exposes an explicit ``infer_multi()`` API, so
this adapter batches up to ``UNLIMITED_MAX_PAGES_PER_CALL`` pages per model call
(``options["max_pages_per_call"]`` can lower it). Transformers runtime only —
``infer_multi`` has no vLLM equivalent, so ``runtime="vllm"`` fails typed.
"""

from __future__ import annotations

from typing import Protocol

from transformers import AutoModelForImageTextToText  # ty: ignore[unresolved-import] — extra not installed in dev/CI; heavy by contract

from parsecraft.backends.errors import BackendError
from parsecraft.backends.ocr._common import (
    analyze_source,
    convert_pages,
    count_pages,
    pil_image,
    rasterize_page,
    runtime_choice,
)
from parsecraft.backends.ocr._models import (
    OCR_BACKEND_VERSION,
    UNLIMITED_ASSET,
    UNLIMITED_CAPABILITIES,
    UNLIMITED_EXTRA,
    UNLIMITED_MAX_PAGES_PER_CALL,
    UNLIMITED_MODEL_ID,
    UNLIMITED_NAME,
    UNLIMITED_REVISION,
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

#: Adapter prompt: complete transcription, section by section, reading order.
PROMPT = "Transcribe this document page completely, section by section, in reading order. Never truncate; continue until every region is covered."

#: Plan option: lower the per-call batch ceiling on constrained hardware.
_MAX_PAGES_OPTION = "max_pages_per_call"


class LongHorizonModel(Protocol):
    """The plan's ``infer_multi()`` surface: bounded multi-page transcription."""

    def infer_multi(self, *, images: list[object], prompt: str, max_new_tokens: int | None) -> list[str]:
        """Transcribe a batch of page images, one result per image."""
        ...


class _UnlimitedBackend:
    """Instantiated Unlimited-OCR backend: bound model + batched light orchestration."""

    name = UNLIMITED_NAME
    capabilities: BackendCapabilities = UNLIMITED_CAPABILITIES

    def __init__(self, config: BackendConfig, model: LongHorizonModel) -> None:
        self._config = config
        self._model = model

    def convert(self, request: ConversionRequest) -> BackendResult:
        cache: dict[int, str] = {}
        total: int | None = None

        def _infer(number: int, inner: ConversionRequest) -> str:
            nonlocal total
            if number in cache:
                return cache[number]
            if total is None:
                total = count_pages(inner.source)
            batch = list(range(number, min(number + self._batch_size() - 1, total) + 1))
            images = [pil_image(rasterize_page(inner.source, n), extra=UNLIMITED_EXTRA) for n in batch]
            texts = self._model.infer_multi(
                images=images,
                prompt=PROMPT,
                max_new_tokens=inner.max_context_tokens,
            )
            if len(texts) != len(batch):
                msg = f"infer_multi returned {len(texts)} results for {len(batch)} pages"
                raise RuntimeError(msg)
            cache.update(zip(batch, texts, strict=True))
            return cache[number]

        return convert_pages(
            backend_name=UNLIMITED_NAME,
            backend_version=OCR_BACKEND_VERSION,
            asset=UNLIMITED_ASSET,
            source=request.source,
            request=request,
            infer_page=_infer,
        )

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        return analyze_source(source)

    def _batch_size(self) -> int:
        """Per-call page ceiling: option override, capped by the plan default."""
        raw = self._config.options.get(_MAX_PAGES_OPTION, UNLIMITED_MAX_PAGES_PER_CALL)
        if not isinstance(raw, int) or raw < 1:
            msg = f"{_MAX_PAGES_OPTION!r} must be a positive integer, got {raw!r}"
            raise BackendError(msg)
        return min(raw, UNLIMITED_MAX_PAGES_PER_CALL)


def create(config: BackendConfig) -> DocumentBackend:
    """Build the backend — the sanctioned heavy-import boundary."""
    if runtime_choice(config) == "vllm":
        msg = f"backend {UNLIMITED_NAME!r} supports runtime='transformers' only (infer_multi long-horizon API)"
        raise BackendError(msg)
    return _UnlimitedBackend(config, _load_model())


def _load_model() -> LongHorizonModel:
    """Load the pinned model; load failures become typed BackendErrors."""
    try:
        return AutoModelForImageTextToText.from_pretrained(
            UNLIMITED_MODEL_ID,
            revision=UNLIMITED_REVISION,
            torch_dtype="auto",
            device_map="auto",
        )
    except Exception as exc:  # model/stack load boundary — typed, never raw
        msg = f"failed to load model {UNLIMITED_MODEL_ID!r} at revision {UNLIMITED_REVISION[:12]}: {type(exc).__name__}: {exc}"
        raise BackendError(msg) from exc
