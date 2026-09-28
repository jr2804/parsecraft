"""Heavy Unlimited-OCR implementation — Transformers imported at top level by design.

Loaded only from the light factory at instantiation (``importlib``-based, never
an inline import). Model pin + license: ``_models.UNLIMITED_ASSET`` (verified
against the HF API 2026-09-27).

Call shapes verified against the model card (README, 2026-09-27):
- modeling ported to ``_vendored/unlimited`` (pc-4u7.36) — no
  ``trust_remote_code``; see ``_vendored/README.md``;
- ``infer_multi(tokenizer, prompt='<image>Multi page parsing.', image_files=[...],
  output_path=<dir>, image_size=1024, max_length=..., no_repeat_ngram_size=35,
  ngram_window=1024)`` returns ONE long-horizon generation whose pages are
  ``<PAGE>``-separated;
- multi-page runs use base mode (``image_size=1024``), so rasterized pages are
  written to a temporary directory and passed as file paths.

Transformers-only: ``infer_multi`` has no vLLM equivalent, so ``runtime="vllm"``
fails typed. ``options["max_pages_per_call"]`` lowers the per-call ceiling
(plan default is deliberately small).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Protocol

from transformers import AutoTokenizer  # ty: ignore[unresolved-import] — extra not installed in dev/CI; heavy by contract

from parsecraft.backends.errors import BackendError
from parsecraft.backends.ocr._common import (
    analyze_source,
    convert_pages,
    count_pages,
    model_source_and_revision,
    rasterize_page,
    require_transformers,
    runtime_choice,
)
from parsecraft.backends.ocr._models import (
    OCR_BACKEND_VERSION,
    UNLIMITED_ASSET,
    UNLIMITED_CAPABILITIES,
    UNLIMITED_MAX_PAGES_PER_CALL,
    UNLIMITED_NAME,
)
from parsecraft.backends.ocr._vendored.unlimited.modeling_unlimitedocr import (
    UnlimitedOCRConfig,
    UnlimitedOCRForCausalLM,
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

#: Card-verified prompt for multi-page parsing (README ``infer_multi`` example).
PROMPT = "<image>Multi page parsing."
#: Card-verified base-mode settings for multi-page/PDF runs.
_IMAGE_SIZE = 1024
_NO_REPEAT_NGRAM_SIZE = 35
_NGRAM_WINDOW = 1024
_DEFAULT_MAX_LENGTH = 32768
#: The model separates pages inside one generation with this sentinel.
_PAGE_SEPARATOR = "<PAGE>"
#: Plan option: lower the per-call batch ceiling on constrained hardware.
_MAX_PAGES_OPTION = "max_pages_per_call"


class LongHorizonModel(Protocol):
    """The card's ``infer_multi()`` surface (vendored modeling, pc-4u7.36)."""

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
        """Run one long-horizon generation over a batch of page images."""
        ...


class _UnlimitedBackend:
    """Instantiated Unlimited-OCR backend: bound model + batched light orchestration."""

    name = UNLIMITED_NAME
    capabilities: BackendCapabilities = UNLIMITED_CAPABILITIES

    def __init__(self, config: BackendConfig, model: LongHorizonModel, tokenizer: object) -> None:
        self._config = config
        self._model = model
        self._tokenizer = tokenizer

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
            texts = self._run_batch(inner, batch)
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

    def _run_batch(self, request: ConversionRequest, batch: list[int]) -> list[str]:
        """Rasterize the batch to temp PNGs and run one ``infer_multi`` generation."""
        with tempfile.TemporaryDirectory(prefix="parsecraft-ocr-") as tmp:
            image_files = []
            for number in batch:
                page_path = Path(tmp) / f"page-{number:04d}.png"
                page_path.write_bytes(rasterize_page(request.source, number))
                image_files.append(str(page_path))
            max_length = _DEFAULT_MAX_LENGTH if request.max_context_tokens is None else min(_DEFAULT_MAX_LENGTH, request.max_context_tokens)
            raw, _tokens = self._model.infer_multi(
                self._tokenizer,
                prompt=PROMPT,
                image_files=image_files,
                output_path=tmp,
                image_size=_IMAGE_SIZE,
                max_length=max_length,
                no_repeat_ngram_size=_NO_REPEAT_NGRAM_SIZE,
                ngram_window=_NGRAM_WINDOW,
            )
            return _page_texts(raw, batch=len(batch))

    def _batch_size(self) -> int:
        """Per-call page ceiling: option override, capped by the plan default."""
        raw = self._config.options.get(_MAX_PAGES_OPTION, UNLIMITED_MAX_PAGES_PER_CALL)
        if not isinstance(raw, int) or raw < 1:
            msg = f"{_MAX_PAGES_OPTION!r} must be a positive integer, got {raw!r}"
            raise BackendError(msg)
        return min(raw, UNLIMITED_MAX_PAGES_PER_CALL)


def _page_texts(raw: object, *, batch: int) -> list[str]:
    """Split the long-horizon output into per-page texts (``<PAGE>`` separated)."""
    if not isinstance(raw, str):
        msg = f"infer_multi returned {type(raw).__name__}, expected str"
        raise TypeError(msg)
    segments = [segment.strip() for segment in raw.split(_PAGE_SEPARATOR)[1:]]
    if not segments:
        # Single-page runs may come back without separators: the text IS the page.
        segments = [raw.strip()] if batch == 1 else []
    if len(segments) != batch:
        msg = f"infer_multi returned {len(segments)} page segments for {batch} pages"
        raise RuntimeError(msg)
    return segments


def create(config: BackendConfig) -> DocumentBackend:
    """Build the backend — the sanctioned heavy-import boundary."""
    if runtime_choice(config) == "vllm":
        msg = f"backend {UNLIMITED_NAME!r} supports runtime='transformers' only (infer_multi long-horizon API)"
        raise BackendError(msg)
    return _UnlimitedBackend(config, *_load(config))


def _load(config: BackendConfig) -> tuple[LongHorizonModel, object]:
    """Load the pinned model + tokenizer; load failures become typed BackendErrors.

    Assets arrive through ``AssetManager`` (managed local dir, no implicit hub
    fetch); an unpinned descriptor falls back to the hub id + pinned revision.
    """
    source, revision = model_source_and_revision(UNLIMITED_ASSET, config)
    # Vendored modeling (pc-4u7.36): explicit config+model classes, no
    # trust_remote_code (the repo's remote code is 4.x-only) — see
    # _vendored/README.md. The tokenizer is stock LlamaTokenizerFast.
    require_transformers()
    config_kwargs: dict[str, object] = {}
    model_kwargs: dict[str, object] = {
        "use_safetensors": True,
        "dtype": "bfloat16",
        "device_map": "auto",
    }
    tokenizer_kwargs: dict[str, object] = {}
    if revision is not None:
        config_kwargs["revision"] = revision
        model_kwargs["revision"] = revision
        tokenizer_kwargs["revision"] = revision
    try:
        config = UnlimitedOCRConfig.from_pretrained(source, **config_kwargs)
        model = UnlimitedOCRForCausalLM.from_pretrained(source, config=config, **model_kwargs)
        model.eval()
        tokenizer = AutoTokenizer.from_pretrained(source, **tokenizer_kwargs)
    except Exception as exc:  # model/stack load boundary — typed, never raw
        where = f" at revision {revision[:12]}" if revision is not None else ""
        msg = f"failed to load model {source!r}{where}: {type(exc).__name__}: {exc}"
        raise BackendError(msg) from exc
    return model, tokenizer
