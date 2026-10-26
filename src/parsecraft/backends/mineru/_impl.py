"""Heavy native MinerU2.5 implementation — transformers imported at top level by design.

Loaded only from the light factory at instantiation (importlib-based, never an
inline import). The pinned checkpoint is a stock
``Qwen2VLForConditionalGeneration``; the two-step protocol (layout detection,
then per-block recognition) is ported from ``mineru-vl-utils`` (Apache-2.0,
MinerU Team — attribution in ADR-0007). The ``mineru`` pip package is not a
dependency of this backend and never runs.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from time import monotonic
from typing import Protocol

import pypdfium2 as pdfium
from PIL import Image
from transformers import (
    AutoConfig,
    AutoImageProcessor,
    AutoProcessor,
    PretrainedConfig,
    Qwen2VLForConditionalGeneration,
)

from parsecraft.backends.errors import BackendError
from parsecraft.backends.mineru._content import (
    TableTextExtractor,
    is_non_text,
    markdown_table_to_html,
    otsl_to_html,
    parse_image_analysis,
    wrap_equation,
)
from parsecraft.backends.mineru._layout import (
    KNOWN_TYPES,
    SKIP_EXTRACTION,
    LayoutBlock,
    parse_layout,
    strip_covered,
)
from parsecraft.backends.mineru.mineru import (
    DESCRIPTOR,
    MINERU_ASSET,
    MINERU_BACKEND_VERSION,
    MINERU_NAME,
)
from parsecraft.backends.ocr._common import (
    DEFAULT_PAGE_MAX_NEW_TOKENS,
    chat_prompt,
    model_source_and_revision,
    rasterize_page,
    require_transformers,
    tokenizer_load_kwargs,
)
from parsecraft.backends.options import coerce_option_value
from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendConfig,
    BackendRef,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
    SourceDocument,
)
from parsecraft.backends.source import path_from_file_uri
from parsecraft.ir.models import (
    AssetKind,
    BBox,
    ChunkKind,
    ExtractedAsset,
    FailureCode,
    PageRange,
    PageResult,
    PageSignal,
    PassFailure,
    PassKind,
    StructuredChunk,
    utcnow,
)

# ── Protocol constants (Apache-2.0, mineru-vl-utils) ─────────────────────────

#: Layout-detection prompt; the model answers with tagged boxes.
LAYOUT_PROMPT = "\nLayout Detection:"

#: Per-type recognition prompts; anything unlisted gets the text default.
RECOGNITION_PROMPTS: dict[str, str] = {
    "table": "\nTable Recognition:",
    "equation": "\nFormula Recognition:",
    "image": "\nImage Analysis:",
    "chart": "\nImage Analysis:",
}
DEFAULT_RECOGNITION_PROMPT = "\nText Recognition:"

#: The layout input resolution the checkpoint was trained on.
_LAYOUT_SIZE: tuple[int, int] = (1036, 1036)

#: Types whose recognition output feeds the image-analysis classifier.
_IMAGE_ANALYSIS_TYPES: frozenset[str] = frozenset({"image", "chart"})


class MineruEffort(StrEnum):
    """Effort dial — the only quality knob the native backend has."""

    FLASH = "flash"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


_DEFAULT_EFFORT = MineruEffort.FLASH

#: Generation budget per recognition pass, by effort (tokens). The request's
#: ``max_context_tokens`` always wins over the dial.
EFFORT_BUDGETS: dict[MineruEffort, int] = {
    MineruEffort.FLASH: DEFAULT_PAGE_MAX_NEW_TOKENS,
    MineruEffort.MEDIUM: DEFAULT_PAGE_MAX_NEW_TOKENS,
    MineruEffort.HIGH: 4096,
    MineruEffort.XHIGH: 8192,
}

#: Layout type → IR chunk kind.
_KIND_BY_TYPE: dict[str, ChunkKind] = {
    "text": ChunkKind.PARAGRAPH,
    "list_item": ChunkKind.LIST,
    "index": ChunkKind.LIST,
    "table": ChunkKind.TABLE,
    "equation": ChunkKind.FORMULA,
    "formula_number": ChunkKind.PARAGRAPH,
    "code": ChunkKind.CODE,
    "algorithm": ChunkKind.CODE,
    "image": ChunkKind.FIGURE,
    "chart": ChunkKind.FIGURE,
    "image_caption": ChunkKind.CAPTION,
    "table_caption": ChunkKind.CAPTION,
    "code_caption": ChunkKind.CAPTION,
    "caption": ChunkKind.CAPTION,
    "header": ChunkKind.HEADER,
    "footer": ChunkKind.FOOTER,
    "aside_text": ChunkKind.PARAGRAPH,
    "ref_text": ChunkKind.PARAGRAPH,
    "phonetic": ChunkKind.PARAGRAPH,
    "table_footnote": ChunkKind.PARAGRAPH,
    "image_footnote": ChunkKind.PARAGRAPH,
    "footnote": ChunkKind.PARAGRAPH,
    "page_footnote": ChunkKind.PARAGRAPH,
    "title": ChunkKind.HEADING,
    "doc_title": ChunkKind.HEADING,
    "paragraph_title": ChunkKind.HEADING,
}

#: Heading levels for the title family (the layout protocol distinguishes
#: document titles from section titles, nothing finer).
_HEADING_LEVELS: dict[str, str] = {"doc_title": "1", "title": "1", "paragraph_title": "2"}

#: Page furniture and container blocks that never produce IR chunks.
_DROP_TYPES: frozenset[str] = frozenset({"page_number", "list", "equation_block", "image_block"})


# ── Engine: the loaded checkpoint ────────────────────────────────────────────


class _Tensor(Protocol):
    """The tensor surface generation needs (satisfied by torch)."""

    def to(self, *, device: object = ..., dtype: object = ...) -> _Tensor: ...

    def tolist(self) -> list[list[int]]: ...


class _GenerateOutput(Protocol):
    """The generate() return surface (satisfied by torch)."""

    def tolist(self) -> list[list[int]]: ...


class _Processor(Protocol):
    """The processor surface the engine needs (satisfied by transformers)."""

    def apply_chat_template(self, messages: object, **kwargs: object) -> str: ...

    def __call__(self, **kwargs: object) -> dict[str, _Tensor]: ...

    def batch_decode(self, sequences: object, **kwargs: object) -> list[str]: ...


class _Model(Protocol):
    """The model surface the engine needs (satisfied by transformers)."""

    @property
    def device(self) -> object: ...

    @property
    def dtype(self) -> object: ...

    def generate(self, **kwargs: object) -> _GenerateOutput: ...


class _Engine:
    """Loaded checkpoint + processor: greedy batched generation.

    One sampling recipe for every pass — the upstream per-type penalties are
    logits processors HF ``generate`` does not support, so greedy with
    ``no_repeat_ngram_size`` is the ported remainder. Requests are processed
    in order, one batch at a time: determinism over padding efficiency.
    """

    def __init__(self, model: _Model, processor: _Processor, skip_token_ids: frozenset[int]) -> None:
        self._model = model
        self._processor = processor
        self._skip_token_ids = skip_token_ids

    def generate(self, images: list[Image.Image], prompts: list[str], max_new_tokens: int) -> list[str]:
        """Recognize each image against its prompt; one text per pair."""
        templated = [chat_prompt(self._processor, user_text=prompt) for prompt in prompts]
        inputs = self._processor(text=templated, images=images, padding=True, return_tensors="pt")
        moved = {key: value.to(device=self._model.device, dtype=self._model.dtype) for key, value in inputs.items()}
        output_ids = self._model.generate(
            **moved,
            use_cache=True,
            do_sample=False,
            no_repeat_ngram_size=100,
            repetition_penalty=1.0,
            max_new_tokens=max_new_tokens,
        )
        rows = output_ids.tolist()
        input_rows = moved["input_ids"].tolist()
        results: list[str] = []
        for row, input_row in zip(rows, input_rows, strict=True):
            continuation = [token for token in row[len(input_row) :] if token not in self._skip_token_ids]
            results.append(self._processor.batch_decode([continuation], skip_special_tokens=False, clean_up_tokenization_spaces=False)[0])
        return results


def _load_engine(config: BackendConfig) -> _Engine:
    """Load the pinned checkpoint — assets ensured, versions checked, errors typed."""
    require_transformers()
    model_source, revision = model_source_and_revision(MINERU_ASSET, config)
    try:
        model = _load_model(model_source, revision)
        processor = _load_processor(model_source, revision)
    except BackendError:
        raise
    except Exception as exc:  # model/stack load boundary — typed, never raw
        where = f" at revision {revision[:12]}" if revision is not None else ""
        msg = f"failed to load model {model_source!r}{where}: {type(exc).__name__}: {exc}"
        raise BackendError(msg) from exc
    return _Engine(model, processor, _skip_token_ids(model, processor))


def _load_model(model_source: str, revision: str | None) -> _Model:
    """Load the Qwen2VL checkpoint with the tie-weights fix.

    transformers 5.10 randomly re-initializes an unsaved ``lm_head`` unless
    the text config's ``tie_word_embeddings`` is synced with the raw config
    dict (verified upstream in ``mineru-vl-utils`` ``transformers_loading``).
    """
    if revision is None:
        config = AutoConfig.from_pretrained(model_source)
        raw_config, _ = PretrainedConfig.get_config_dict(model_source)
    else:
        config = AutoConfig.from_pretrained(model_source, revision=revision)
        raw_config, _ = PretrainedConfig.get_config_dict(model_source, revision=revision)
    text_config = config.get_text_config(decoder=True)
    tied = raw_config.get("tie_word_embeddings")
    if tied is None:
        tied = getattr(text_config, "tie_word_embeddings", None)
    if tied is not None:
        text_config.tie_word_embeddings = tied
        config.tie_word_embeddings = tied
    if revision is None:
        return Qwen2VLForConditionalGeneration.from_pretrained(model_source, config=config, device_map="auto", dtype="auto")
    return Qwen2VLForConditionalGeneration.from_pretrained(model_source, config=config, device_map="auto", dtype="auto", revision=revision)


def _load_processor(model_source: str, revision: str | None) -> _Processor:
    """Load the processor; the image side goes through the torchvision backend
    (the read-only video processor cannot carry the backend kwarg).
    """
    if revision is None:
        processor = AutoProcessor.from_pretrained(model_source, **tokenizer_load_kwargs())
        processor.image_processor = AutoImageProcessor.from_pretrained(model_source, backend="torchvision")
        return processor
    processor = AutoProcessor.from_pretrained(model_source, revision=revision, **tokenizer_load_kwargs())
    processor.image_processor = AutoImageProcessor.from_pretrained(model_source, backend="torchvision", revision=revision)
    return processor


def _skip_token_ids(model: _Model, processor: _Processor) -> frozenset[int]:
    """Collect bos/eos/pad ids from every place transformers keeps them."""
    ids: set[int] = set()
    holders = [
        getattr(model, "generation_config", None),
        getattr(model, "config", None),
        getattr(processor, "tokenizer", None),
    ]
    for holder in holders:
        if holder is None:
            continue
        for attr in ("bos_token_id", "eos_token_id", "pad_token_id"):
            value = getattr(holder, attr, None)
            if isinstance(value, int):
                ids.add(value)
            elif isinstance(value, (list, tuple)):
                ids.update(token for token in value if isinstance(token, int))
    return frozenset(ids)


# ── Backend ──────────────────────────────────────────────────────────────────


class MineruBackend:
    """Native two-step conversion: layout detection, then block recognition."""

    name = MINERU_NAME
    capabilities = DESCRIPTOR.capabilities

    def __init__(self, config: BackendConfig) -> None:
        self._config = config
        self._effort = _effort(config)
        self._image_analysis = _image_analysis(config)
        # FLASH stays weight-free until a weak-text page forces the VLM;
        # every other effort loads (and fails fast) here.
        self._engine: _Engine | None = None if self._effort is MineruEffort.FLASH else _load_engine(config)

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        """Cheap model-free pypdfium2 signals — unchanged by the native path."""
        payload = source_bytes(source)
        signals = _pdf_signals(payload)
        if not signals:
            signals = [PageSignal(page_number=1, has_native_text=False, text_chars=0, image_count=0, blank=True)]
        return AnalysisResult(
            source_hash=hashlib.sha256(payload).hexdigest(),
            page_count=len(signals),
            signals=signals,
        )

    def convert(self, request: ConversionRequest) -> BackendResult:
        """Convert the requested window page by page, honoring every bound."""
        started = monotonic()
        payload = source_bytes(request.source)
        signals = _pdf_signals(payload)
        page_count = len(signals) or 1
        window = _window(request.page_range, page_count)
        if window is None:
            assert request.page_range is not None  # noqa: S101 — narrowed by _window
            failure = _failure(
                request,
                started,
                FailureCode.INVALID_INPUT,
                f"page range {request.page_range.start}-{request.page_range.end} outside the {page_count}-page document",
            )
            return _result([], [failure], started)
        if _cancelled(request):
            failure = _failure(request, started, FailureCode.CANCELLED, "cancelled before conversion")
            return _result([], [failure], started)

        pages: list[PageResult] = []
        failures: list[PassFailure] = []
        emitted = 0
        for number in window:
            if _cancelled(request):
                failures.append(_failure(request, started, FailureCode.CANCELLED, f"cancelled before page {number}"))
                break
            if _timed_out(request, started):
                failures.append(_failure(request, started, FailureCode.TIMEOUT, f"deadline passed before page {number}"))
                break
            try:
                page, page_chars = self._convert_page(payload, number, _signal(signals, number), request)
            except Exception as exc:  # per-page boundary: one bad page never sinks the run
                failures.append(
                    _failure(
                        request,
                        started,
                        FailureCode.BACKEND_ERROR,
                        f"page {number}: {type(exc).__name__}: {exc}",
                    )
                )
                pages.append(PageResult(page_number=number))
                continue
            if _timed_out(request, started):
                failures.append(_failure(request, started, FailureCode.TIMEOUT, f"deadline passed during page {number}"))
                break
            if request.max_output_chars is not None and emitted + page_chars > request.max_output_chars:
                failures.append(
                    _failure(
                        request,
                        started,
                        FailureCode.BUDGET_EXCEEDED,
                        f"page {number} would exceed the {request.max_output_chars}-character output budget",
                    )
                )
                break
            pages.append(page)
            emitted += page_chars
        return _result(pages, failures, started)

    # ── Per-page conversion ──────────────────────────────────────────────

    def _convert_page(
        self,
        payload: bytes,
        number: int,
        signal: PageSignal,
        request: ConversionRequest,
    ) -> tuple[PageResult, int]:
        """One page: the VLM two-step, or the weight-free text layer for FLASH."""
        width, height = _page_size(payload, number)
        if self._effort is MineruEffort.FLASH and signal.has_native_text:
            chunks = _text_chunks(payload, number)
            page = PageResult(page_number=number, width=width, height=height, blocks=chunks)
            return page, sum(len(chunk.content) for chunk in chunks)
        png = _raster(payload, number)
        page_image = Image.open(BytesIO(png)).convert("RGB")
        chunks, assets = self._vlm_page(page_image, number, width, height, request)
        page = PageResult(page_number=number, width=width, height=height, blocks=chunks, assets=assets)
        return page, sum(len(chunk.content) for chunk in chunks)

    def _vlm_page(
        self,
        page_image: Image.Image,
        number: int,
        width_pts: float,
        height_pts: float,
        request: ConversionRequest,
    ) -> tuple[list[StructuredChunk], list[ExtractedAsset]]:
        """The two-step protocol on one rendered page."""
        engine = self._require_engine()
        budget = request.max_context_tokens or EFFORT_BUDGETS[self._effort]
        layout_input = page_image.resize(_LAYOUT_SIZE, Image.Resampling.BICUBIC)
        layout_text = engine.generate([layout_input], [LAYOUT_PROMPT], budget)[0]
        blocks = strip_covered(parse_layout(layout_text))

        # Figure crops exist whenever the block does — the crop is the asset.
        figure_crops = {id(block): _crop_block(page_image, block) for block in blocks if block.type in _IMAGE_ANALYSIS_TYPES}
        extracted = [block for block in blocks if block.type not in SKIP_EXTRACTION and (block.type not in _IMAGE_ANALYSIS_TYPES or self._image_analysis)]
        images = [figure_crops.get(id(block)) or _crop_block(page_image, block) for block in extracted]
        prompts = [RECOGNITION_PROMPTS.get(block.type, DEFAULT_RECOGNITION_PROMPT) for block in extracted]
        outputs = engine.generate(images, prompts, budget) if images else []
        contents = {id(block): output for block, output in zip(extracted, outputs, strict=True)}
        return _blocks_to_chunks(blocks, contents, figure_crops, number, width_pts, height_pts, self._image_analysis)

    def _require_engine(self) -> _Engine:
        """FLASH defers the weights until a page actually needs them."""
        if self._engine is None:
            self._engine = _load_engine(self._config)
        return self._engine


# ── Block → IR mapping ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Mapped:
    """One block's mapping decision: kind, text, metadata, optional table rows."""

    kind: ChunkKind
    text: str
    metadata: dict[str, str]
    rows: tuple[tuple[str, ...], ...] | None = None


def _blocks_to_chunks(
    blocks: list[LayoutBlock],
    contents: dict[int, str],
    figure_crops: dict[int, Image.Image],
    number: int,
    width_pts: float,
    height_pts: float,
    image_analysis: bool,
) -> tuple[list[StructuredChunk], list[ExtractedAsset]]:
    """Map layout blocks (with recognized content) to IR chunks and assets."""
    chunks: list[StructuredChunk] = []
    assets: list[ExtractedAsset] = []
    text_tail: StructuredChunk | None = None
    for block in blocks:
        mapped = _map_block(block, contents.get(id(block)), image_analysis)
        if mapped is None:
            continue
        if block.merge_prev and mapped.kind is ChunkKind.PARAGRAPH and text_tail is not None:
            text_tail.content = f"{text_tail.content}\n{mapped.text}"
            continue
        bbox = _block_bbox(block, width_pts, height_pts)
        chunk = StructuredChunk(
            id=f"mineru-{number}-b{len(chunks)}",
            kind=mapped.kind,
            content=mapped.text,
            page_number=number,
            reading_order=len(chunks),
            bbox=bbox,
            rows=mapped.rows,
            metadata={"backend": MINERU_NAME, **mapped.metadata},
        )
        if block.type in _IMAGE_ANALYSIS_TYPES and id(block) in figure_crops:
            asset = _image_asset(figure_crops[id(block)], bbox, number, len(assets))
            assets.append(asset)
            chunk.metadata["asset_id"] = asset.id
        chunks.append(chunk)
        text_tail = chunk if mapped.kind is ChunkKind.PARAGRAPH else None
    return chunks, assets


def _map_block(
    block: LayoutBlock,
    content: str | None,
    image_analysis: bool,
) -> _Mapped | None:
    """One block's mapping; ``None`` drops the block."""
    if block.type in _DROP_TYPES or block.type not in KNOWN_TYPES or block.type not in _KIND_BY_TYPE:
        return None
    kind = _KIND_BY_TYPE[block.type]
    if kind is ChunkKind.FIGURE:
        # A figure lives even without recognized content — the crop is the asset.
        if image_analysis and content and content.strip():
            return _image_analysis_content(content)
        return _Mapped(ChunkKind.FIGURE, "", {})
    if is_non_text(content):
        return None
    text = (content or "").strip()
    metadata: dict[str, str] = {}
    if block.type in _HEADING_LEVELS:
        metadata["heading_level"] = _HEADING_LEVELS[block.type]
    if kind is ChunkKind.FORMULA:
        text = wrap_equation(text)
    elif kind is ChunkKind.TABLE:
        extractor = TableTextExtractor()
        extractor.feed(otsl_to_html(text))
        extractor.close()
        return _Mapped(kind, extractor.render(), metadata, extractor.rows())
    return _Mapped(kind, text, metadata)


def _image_analysis_content(text: str) -> _Mapped | None:
    """Classified image content: a pure table becomes a table, a pure formula
    an equation, everything else a figure with its caption.
    """
    fields = parse_image_analysis(text)
    classification = fields.get("class", "")
    if classification == "pure_table":
        table = markdown_table_to_html(fields.get("content", "")) or otsl_to_html(fields.get("content", ""))
        extractor = TableTextExtractor()
        extractor.feed(table)
        extractor.close()
        return _Mapped(ChunkKind.TABLE, extractor.render(), {}, extractor.rows())
    if classification == "pure_formula":
        return _Mapped(ChunkKind.FORMULA, wrap_equation(fields.get("content", "")), {})
    caption = fields.get("caption", "")
    return _Mapped(ChunkKind.FIGURE, caption, {}) if caption else None


def _block_bbox(block: LayoutBlock, width_pts: float, height_pts: float) -> BBox | None:
    """Fractional layout coordinates → source points; malformed boxes vanish."""
    try:
        return BBox(
            x0=block.x0 * width_pts,
            y0=block.y0 * height_pts,
            x1=block.x1 * width_pts,
            y1=block.y1 * height_pts,
        )
    except ValueError:
        return None


def _image_asset(crop: Image.Image, bbox: BBox | None, number: int, index: int) -> ExtractedAsset:
    """The cropped region as a content-addressed inline asset."""
    buffer = BytesIO()
    crop.save(buffer, "PNG")
    payload = buffer.getvalue()
    return ExtractedAsset(
        id=f"mineru-{number}-a{index}",
        kind=AssetKind.IMAGE,
        media_type="image/png",
        uri="data:image/png;base64," + base64.b64encode(payload).decode("ascii"),
        bbox=bbox,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _crop_block(page_image: Image.Image, block: LayoutBlock) -> Image.Image:
    """Crop a block's region, rotated upright for recognition."""
    width, height = page_image.size
    x0 = max(0, min(int(block.x0 * width), width))
    y0 = max(0, min(int(block.y0 * height), height))
    x1 = max(0, min(int(block.x1 * width), width))
    y1 = max(0, min(int(block.y1 * height), height))
    crop = page_image.crop((x0, y0, x1, y1))
    rotations = {
        90: Image.Transpose.ROTATE_90,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_270,
    }
    if block.angle in rotations:
        crop = crop.transpose(rotations[block.angle])
    return crop


def _text_chunks(payload: bytes, number: int) -> list[StructuredChunk]:
    """FLASH text path: the text layer split on blank lines, one chunk each."""
    text = _page_text(payload, number).replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    return [
        StructuredChunk(
            id=f"mineru-{number}-b{index}",
            kind=ChunkKind.PARAGRAPH,
            content=paragraph,
            page_number=number,
            reading_order=index,
            metadata={"backend": MINERU_NAME},
        )
        for index, paragraph in enumerate(paragraphs)
    ]


# ── pypdfium2 page access (the base engine, ADR-0008 d10) ───────────────────


def _pdf_signals(payload: bytes) -> list[PageSignal]:
    """Per-page text-layer signals: present, size, blankness."""
    document = pdfium.PdfDocument(payload)
    try:
        signals = []
        for index in range(len(document)):
            page = document[index]
            textpage = page.get_textpage()
            try:
                text = textpage.get_text_range()
            finally:
                textpage.close()
            stripped = text.strip()
            signals.append(
                PageSignal(
                    page_number=index + 1,
                    has_native_text=bool(stripped),
                    text_chars=len(text),
                    image_count=0,
                    blank=not stripped,
                )
            )
        return signals
    finally:
        document.close()


def _page_text(payload: bytes, number: int) -> str:
    """One page's text layer."""
    document = pdfium.PdfDocument(payload)
    try:
        textpage = document[number - 1].get_textpage()
        try:
            return textpage.get_text_range()
        finally:
            textpage.close()
    finally:
        document.close()


def _page_size(payload: bytes, number: int) -> tuple[float, float]:
    """One page's size in points."""
    document = pdfium.PdfDocument(payload)
    try:
        return document[number - 1].get_size()
    finally:
        document.close()


def _raster(payload: bytes, number: int) -> bytes:
    """One page's raster, through the OCR family's shared surface."""
    return rasterize_page(SourceDocument(uri=f"inline://page/{number}", content=payload), number)


# ── Options, bounds, failures ────────────────────────────────────────────────


def _effort(config: BackendConfig) -> MineruEffort:
    raw = coerce_option_value(config.options.get("effort", _DEFAULT_EFFORT.value))
    try:
        return MineruEffort(raw)
    except ValueError:
        expected = ", ".join(effort.value for effort in MineruEffort)
        msg = f"unknown effort {raw!r}; expected one of: {expected}"
        raise BackendError(msg) from None


def _image_analysis(config: BackendConfig) -> bool:
    raw = coerce_option_value(config.options.get("image_analysis", False))
    if not isinstance(raw, bool):
        msg = f"image_analysis option must be a boolean, got {type(raw).__name__}"
        raise BackendError(msg)
    return raw


def _window(page_range: PageRange | None, page_count: int) -> range | None:
    """The 1-based page window; ``None`` means the request is out of bounds."""
    if page_range is None:
        return range(1, page_count + 1)
    if page_range.start > page_count or page_range.end < 1:
        return None
    return range(max(1, page_range.start), min(page_count, page_range.end) + 1)


def _signal(signals: list[PageSignal], number: int) -> PageSignal:
    if 1 <= number <= len(signals):
        return signals[number - 1]
    return PageSignal(page_number=number, has_native_text=False, text_chars=0, image_count=0, blank=True)


def _cancelled(request: ConversionRequest) -> bool:
    return request.cancellation is not None and request.cancellation()


def _timed_out(request: ConversionRequest, started: float) -> bool:
    return request.timeout_s is not None and monotonic() - started > request.timeout_s


def _failure(
    request: ConversionRequest,
    started: float,
    code: FailureCode,
    detail: str,
) -> PassFailure:
    return PassFailure(
        code=code,
        pass_kind=PassKind.NATIVE,
        backend=MINERU_NAME,
        backend_version=MINERU_BACKEND_VERSION,
        budget_s=request.timeout_s if request.timeout_s is not None else 0.0,
        elapsed_s=monotonic() - started,
        detail=detail,
        occurred_at=utcnow(),
    )


def _result(
    pages: list[PageResult],
    failures: list[PassFailure],
    started: float,
) -> BackendResult:
    return BackendResult(
        backend=BackendRef(
            name=MINERU_NAME,
            version=MINERU_BACKEND_VERSION,
            model_id=MINERU_ASSET.model_id,
            model_revision=MINERU_ASSET.model_revision,
        ),
        pages=pages,
        failures=failures,
        elapsed_s=monotonic() - started,
    )


def source_bytes(source: SourceDocument) -> bytes:
    """Payload from memory, or from the source path — typed read errors."""
    if source.content is not None:
        return source.content
    path = path_from_file_uri(source.uri)
    try:
        return path.read_bytes()
    except OSError as exc:
        msg = f"cannot read source {source.uri}: {exc}"
        raise BackendError(msg) from exc


def create(config: BackendConfig) -> DocumentBackend:
    """The sanctioned heavy-import boundary (called by the light factory)."""
    return MineruBackend(config)
