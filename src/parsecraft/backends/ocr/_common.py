"""Shared light machinery for the OCR family: bounds, failures, page access.

No heavy imports live here. Model stacks belong in the ``_<name>_impl`` modules
(loaded only via :func:`load_impl`); optional tooling (PDF rasterization, vLLM)
is imported on demand through :func:`optional_module`, which is ``importlib``
-call based and therefore safe from ``csort``'s inline-import hoisting.
"""

from __future__ import annotations

import hashlib
import importlib
import time
from collections.abc import Callable
from importlib.metadata import version as _package_version
from io import BytesIO
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast, runtime_checkable

from packaging.specifiers import SpecifierSet

from parsecraft.assets.manager import AssetManager
from parsecraft.assets.models import AssetPin
from parsecraft.backends.errors import BackendError, DependencyUnavailableError, UnsupportedDependencyVersionError
from parsecraft.backends.ocr._models import TRANSFORMERS_RANGE
from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendConfig,
    BackendRef,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
    ModelAssetDescriptor,
    PageSignal,
    SourceDocument,
)
from parsecraft.backends.source import path_from_file_uri
from parsecraft.ir.models import (
    ChunkKind,
    FailureCode,
    PageRange,
    PageResult,
    PassFailure,
    PassKind,
    StructuredChunk,
    utcnow,
)

#: Runtimes a backend may select through the ``runtime`` config option.
RUNTIMES: tuple[str, ...] = ("transformers", "vllm")

#: One page transcription: raster bytes + generation cap → page text.
type Transcriber = Callable[[bytes, int | None], str]

_DEFAULT_RUNTIME = "transformers"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_PDF_MAGIC = b"%PDF-"


@runtime_checkable
class ImplModule(Protocol):
    """Contract a heavy ``_<name>_impl`` module must satisfy (public seam)."""

    def create(self, config: BackendConfig) -> DocumentBackend:
        """Instantiate the backend — the sanctioned heavy-import boundary."""
        ...


class PdfPixmap(Protocol):
    """Raster surface of PyMuPDF (extra ``pdf`` — AGPL, explicit opt-in, ADR-0003)."""

    def tobytes(self, output_format: str) -> bytes:
        """Encode the pixmap as PNG bytes."""
        ...


class PdfPage(Protocol):
    """One page of the optional PDF engine."""

    def get_pixmap(self) -> PdfPixmap:
        """Render the page to a pixmap."""
        ...


class PdfDocument(Protocol):
    """Document surface of the optional PDF engine."""

    page_count: int

    def load_page(self, page_number: int) -> PdfPage:
        """Load one zero-based page."""
        ...

    def close(self) -> None:
        """Release the document handle."""
        ...


class VllmCompletion(Protocol):
    """One generated completion of a vLLM output."""

    text: str


class VllmOutput(Protocol):
    """One vLLM generation output."""

    outputs: list[VllmCompletion]


class VllmEngine(Protocol):
    """vLLM engine surface used by the OCR adapters (stub-friendly seam)."""

    def get_tokenizer(self) -> ChatTemplateSource:
        """The engine's tokenizer — renders chat templates for multimodal prompts."""
        ...

    def generate(self, requests: list[dict[str, object]], sampling: object) -> list[VllmOutput]:
        """Generate text for one batch of multimodal prompts."""
        ...


class ChatTemplateSource(Protocol):
    """Tokenizer/processor surface needed to render a model's chat template."""

    def apply_chat_template(self, conversation: list[dict[str, object]], **options: object) -> str:
        """Render a conversation to a templated prompt string."""
        ...


class ImageTextPipeline(Protocol):
    """transformers ``image-text-to-text`` pipeline surface (stub-friendly seam)."""

    @property
    def tokenizer(self) -> ChatTemplateSource:
        """The pipeline's tokenizer — renders chat templates."""
        ...

    def __call__(self, *, text: str, images: object, **generation: object) -> object:
        """Transcribe one page image under ``text``."""
        ...


def load_impl(module_name: str, *, extra: str) -> ImplModule:
    """Import a heavy impl module at instantiation time — the only heavy boundary."""
    module = optional_module(module_name, extra=extra)
    if not isinstance(module, ImplModule):
        msg = f"impl module {module_name!r} must expose create(config)"
        raise BackendError(msg)
    return module


def runtime_choice(config: BackendConfig) -> str:
    """Validate the pluggable ``runtime`` option; defaults to ``transformers``."""
    raw = config.options.get("runtime", _DEFAULT_RUNTIME)
    if not isinstance(raw, str):
        msg = f"runtime option must be a string, got {type(raw).__name__}"
        raise BackendError(msg)
    if raw not in RUNTIMES:
        msg = f"unknown runtime {raw!r}; expected one of: {', '.join(RUNTIMES)}"
        raise BackendError(msg)
    return raw


def load_vllm() -> ModuleType:
    """Import the optional vLLM runtime for ``runtime = "vllm"`` backends."""
    return optional_module("vllm", extra="vllm")


def model_source_and_revision(descriptor: ModelAssetDescriptor, config: BackendConfig) -> tuple[str, str | None]:
    """Managed local dir when assets are ensured; else hub id + pinned revision."""
    local_dir = ensure_assets(descriptor, config)
    if local_dir is not None:
        return local_dir, None
    return descriptor.model_id, descriptor.model_revision


def ensure_assets(descriptor: ModelAssetDescriptor, config: BackendConfig) -> str | None:
    """Acquire a pinned model through :class:`AssetManager`; return its local dir.

    Order of operations is the manager's (offline gate → license acceptance →
    disk check → resumable download → checksum verification). ``config.options``
    may set ``offline`` (boolean) to force the offline gate. An unpinned
    descriptor keeps the legacy hub path (``None``) — integrity data is never
    fabricated.
    """
    if not descriptor.file_pins:
        return None
    offline = config.options.get("offline", False)
    if not isinstance(offline, bool):
        msg = f"offline option must be a boolean, got {type(offline).__name__}"
        raise BackendError(msg)
    pin = AssetPin(
        descriptor=descriptor,
        filenames=[file_pin.path for file_pin in descriptor.file_pins],
        expected_sha256={file_pin.path: file_pin.sha256 for file_pin in descriptor.file_pins},
    )
    manager = AssetManager(offline=offline)
    manager.ensure(pin)
    return str(manager.revision_dir(descriptor.model_id, descriptor.model_revision))


def rasterize_page(source: SourceDocument, page_number: int) -> bytes:
    """Raster for one page: PNG bytes (images pass through unchanged)."""
    payload = source_bytes(source)
    if payload.startswith(_PNG_MAGIC) or payload.startswith(_JPEG_MAGIC):
        return payload
    document = _open_pdf(payload)
    try:
        page = document.load_page(page_number - 1)
        return page.get_pixmap().tobytes("png")
    finally:
        document.close()


def analyze_source(source: SourceDocument) -> AnalysisResult:
    """Deterministic planner signals — no model calls, no layout heuristics.

    OCR backends target page images, so every page reports
    ``has_native_text=False`` (the planner's OCR-routing prior) with one raster
    image per page; ``text_chars`` stays 0 until a conversion pass runs.
    """
    payload = source_bytes(source)
    page_count = count_pages(source)
    signals = [
        PageSignal(
            page_number=number,
            has_native_text=False,
            text_chars=0,
            image_count=1,
            blank=False,
            replacement_char_ratio=None,
        )
        for number in range(1, page_count + 1)
    ]
    return AnalysisResult(
        source_hash=hashlib.sha256(payload).hexdigest(),
        page_count=page_count,
        signals=signals,
    )


def convert_pages(
    *,
    backend_name: str,
    backend_version: str,
    asset: ModelAssetDescriptor,
    source: SourceDocument,
    request: ConversionRequest,
    infer_page: Callable[[int, ConversionRequest], str],
) -> BackendResult:
    """Run ``infer_page`` over the requested window, honoring every request bound.

    Bounds: ``page_range`` (out-of-window → ``INVALID_INPUT``), ``timeout_s``
    (``TIMEOUT``), ``cancellation`` (``CANCELLED``), ``max_output_chars``
    (``BUDGET_EXCEEDED``). Per-page inference errors become ``BACKEND_ERROR``
    records for that page only and the run continues; nothing raises once
    conversion has started. ``max_context_tokens`` is handed through to
    ``infer_page`` as the per-page generation budget (stateless OCR has no
    cross-page context).
    """
    started = time.monotonic()
    reference = BackendRef(
        name=backend_name,
        version=backend_version,
        model_id=asset.model_id,
        model_revision=asset.model_revision,
    )
    budget_s = request.timeout_s if request.timeout_s is not None else 0.0

    def _finish(
        pages: list[PageResult],
        failures: list[PassFailure],
    ) -> BackendResult:
        return BackendResult(
            backend=reference,
            pages=pages,
            failures=failures,
            elapsed_s=time.monotonic() - started,
        )

    try:
        page_count = count_pages(source)
    except BackendError as exc:
        failure = _failure(
            code=FailureCode.INVALID_INPUT,
            backend=backend_name,
            backend_version=backend_version,
            budget_s=budget_s,
            started=started,
            detail=str(exc),
        )
        return _finish([], [failure])
    if page_count == 0:
        failure = _failure(
            code=FailureCode.INVALID_INPUT,
            backend=backend_name,
            backend_version=backend_version,
            budget_s=budget_s,
            started=started,
            detail="source has no pages to convert",
        )
        return _finish([], [failure])
    first, last, window = _resolve_window(request, page_count)
    if window is None:
        failure = _failure(
            code=FailureCode.INVALID_INPUT,
            backend=backend_name,
            backend_version=backend_version,
            budget_s=budget_s,
            started=started,
            detail=f"requested page range is outside the document ({page_count} pages)",
            page_range=request.page_range,
        )
        return _finish([], [failure])

    failures: list[PassFailure] = []
    pages: list[PageResult] = []
    deadline = started + request.timeout_s if request.timeout_s is not None else None
    output_chars = 0
    for number in range(first, last + 1):
        if request.cancellation is not None and request.cancellation():
            failures.append(
                _failure(
                    code=FailureCode.CANCELLED,
                    backend=backend_name,
                    backend_version=backend_version,
                    budget_s=budget_s,
                    started=started,
                    detail=f"conversion cancelled before page {number}",
                    page_range=window,
                )
            )
            break
        if deadline is not None and time.monotonic() >= deadline:
            failures.append(
                _failure(
                    code=FailureCode.TIMEOUT,
                    backend=backend_name,
                    backend_version=backend_version,
                    budget_s=budget_s,
                    started=started,
                    detail=f"time budget exhausted at page {number}",
                    page_range=window,
                )
            )
            break
        try:
            text = infer_page(number, request)
        except Exception as exc:  # per-page failures are data, never raised
            failures.append(
                _failure(
                    code=FailureCode.BACKEND_ERROR,
                    backend=backend_name,
                    backend_version=backend_version,
                    budget_s=budget_s,
                    started=started,
                    detail=f"page {number}: {type(exc).__name__}: {exc}",
                    page_range=PageRange(start=number, end=number),
                )
            )
            continue
        output_chars += len(text)
        if request.max_output_chars is not None and output_chars > request.max_output_chars:
            failures.append(
                _failure(
                    code=FailureCode.BUDGET_EXCEEDED,
                    backend=backend_name,
                    backend_version=backend_version,
                    budget_s=budget_s,
                    started=started,
                    detail=f"output budget of {request.max_output_chars} chars exceeded at page {number}",
                    page_range=window,
                )
            )
            break
        pages.append(_render_page(backend_name, number, text))
    return _finish(pages, failures)


def count_pages(source: SourceDocument) -> int:
    """Page count without running the model: images are one page, PDFs defer to the PDF engine."""
    payload = source_bytes(source)
    if payload.startswith(_PNG_MAGIC) or payload.startswith(_JPEG_MAGIC):
        return 1
    if not payload.startswith(_PDF_MAGIC):
        msg = f"unsupported source {source.uri!r}: expected a PDF or a PNG/JPEG image"
        raise BackendError(msg)
    document = _open_pdf(payload)
    try:
        return int(document.page_count)
    finally:
        document.close()


def source_bytes(source: SourceDocument) -> bytes:
    """Raw payload: in-memory ``content``, else a local ``file://`` read."""
    if source.content is not None:
        return source.content
    if "://" in source.uri and not source.uri.startswith("file://"):
        msg = f"source {source.uri!r} must carry in-memory content (only file:// URIs are read from disk)"
        raise BackendError(msg)
    path = path_from_file_uri(source.uri) if source.uri.startswith("file://") else Path(source.uri)
    try:
        return path.read_bytes()
    except OSError as exc:
        msg = f"cannot read source {source.uri!r}: {exc}"
        raise BackendError(msg) from exc


def load_transformers_pipeline(
    factory: Callable[..., ImageTextPipeline],
    *,
    model_source: str,
    model_revision: str | None,
    trust_remote_code: bool = False,
) -> ImageTextPipeline:
    """Build the image-to-text pipeline; load failures become typed BackendErrors.

    ``model_source`` is a managed local directory (revision omitted — the
    files were pinned and verified by :func:`ensure_assets`) or a hub id
    together with its pinned ``model_revision``.
    """
    require_transformers()
    kwargs: dict[str, object] = {
        "task": "image-text-to-text",
        "model": model_source,
        "device_map": "auto",
        "dtype": "auto",  # transformers>=5 removed torch_dtype; the >=4.56 floor accepts dtype
        "trust_remote_code": trust_remote_code,
    }
    if model_revision is not None:
        kwargs["revision"] = model_revision
    try:
        return factory(**kwargs)
    except Exception as exc:  # model/stack load boundary — typed, never raw
        where = f" at revision {model_revision[:12]}" if model_revision is not None else ""
        msg = f"failed to load model {model_source!r}{where}: {type(exc).__name__}: {exc}"
        raise BackendError(msg) from exc


def require_transformers() -> None:
    """Typed guard: the installed transformers must satisfy the unified window.

    Runs on every model load so a host with an incompatible version gets an
    actionable :class:`UnsupportedDependencyVersionError` instead of a crash
    deep inside weight loading. ``packaging`` ships with transformers and is
    importable by the time this runs; the import stays lazy either way.
    """
    actual = _package_version("transformers")
    # SpecifierSet.contains() returns False for versions it cannot parse (it does
    # not raise), so garbage versions land in the same typed error below.
    satisfied = SpecifierSet(TRANSFORMERS_RANGE, prereleases=True).contains(actual, prereleases=True)
    if not satisfied:
        raise UnsupportedDependencyVersionError("transformers", actual, TRANSFORMERS_RANGE)


def chat_prompt(
    source: ChatTemplateSource,
    *,
    user_text: str,
    system: str | None = None,
    enable_thinking: bool | None = None,
) -> str:
    """Render the model's chat prompt with its image slot — the shape models require.

    A raw prompt yields ``Image features and image tokens do not match, tokens: 0``
    (verified live on OvisOCR2): the processor only inserts image tokens when the
    templated conversation carries the image content part. ``enable_thinking=False``
    matches templates exposing a thinking switch (qwen3_5, per the OvisOCR2 card);
    templates without it raise :class:`TypeError` and are retried plain.
    """
    messages: list[dict[str, object]] = []
    if system is not None:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": [{"type": "image"}, {"type": "text", "text": user_text}]})
    if enable_thinking is None:
        return source.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    try:
        return source.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=enable_thinking,
        )
    except TypeError:
        return source.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)


def transformers_transcriber(pipe: ImageTextPipeline, *, prompt: str, image_extra: str) -> Transcriber:
    """Bind an (already chat-templated) prompt to a pipeline: image bytes → page text.

    The pipeline's image loader accepts URLs, base64, paths, or PIL images —
    but NOT raw bytes (verified against transformers 5.17) — so each raster is
    decoded to a PIL image first.
    """

    def transcribe(image: bytes, max_new_tokens: int | None) -> str:
        generation: dict[str, object] = {} if max_new_tokens is None else {"max_new_tokens": max_new_tokens}
        text = extract_generated(pipe(text=prompt, images=pil_image(image, extra=image_extra), **generation))
        # The pipeline echoes the templated prompt inside generated_text (verified
        # live on OvisOCR2 + transformers 5.17); removeprefix is a no-op if absent.
        return text.removeprefix(prompt)

    return transcribe


def vllm_transcriber(
    engine: VllmEngine,
    *,
    module: ModuleType,
    prompt: str,
    image_extra: str,
) -> Transcriber:
    """Bind a prompt to a vLLM engine: image bytes → page text (``runtime='vllm'``)."""

    def transcribe(image: bytes, max_new_tokens: int | None) -> str:
        sampling = module.SamplingParams(max_tokens=max_new_tokens or 2048, temperature=0.0)
        request: list[dict[str, object]] = [{"prompt": prompt, "multi_modal_data": {"image": pil_image(image, extra=image_extra)}}]
        outputs = engine.generate(request, sampling)
        return str(outputs[0].outputs[0].text)

    return transcribe


def pil_image(payload: bytes, *, extra: str) -> object:
    """Decode PNG/JPEG bytes to a PIL image (pillow ships with the OCR extras)."""
    image_module = optional_module("PIL.Image", extra=extra)
    return image_module.open(BytesIO(payload))


def extract_generated(result: object) -> str:
    """Normalize pipeline output: chat part lists, plain strings, or garbage.

    Pipeline results are JSON-shaped (dicts/lists), not typed models, hence the
    structural checks; anything unexpected raises and becomes a per-page
    ``BACKEND_ERROR`` record in :func:`convert_pages`.
    """
    if not isinstance(result, list) or not result:
        msg = f"unexpected pipeline output: {type(result).__name__}"
        raise RuntimeError(msg)
    first = result[0]
    if not isinstance(first, dict):
        msg = f"unexpected pipeline entry: {type(first).__name__}"
        raise RuntimeError(msg)
    generated = first.get("generated_text")
    if isinstance(generated, str):
        return generated
    if isinstance(generated, list):
        contents: list[str] = []
        for part in generated:
            if isinstance(part, dict):
                content = part.get("content")
                if isinstance(content, str):
                    contents.append(content)
        if contents:
            return contents[-1]
    msg = f"unexpected generated_text shape: {type(generated).__name__}"
    raise RuntimeError(msg)


def _open_pdf(payload: bytes) -> PdfDocument:
    """Open an in-memory PDF with PyMuPDF (extra ``pdf`` — AGPL, explicit opt-in)."""
    try:
        pymupdf = importlib.import_module("pymupdf")
    except ImportError as exc:
        msg = (
            "PDF input needs PyMuPDF — pip install 'parsecraft[pdf]' alongside your 'ocr-*' extra "
            "(PyMuPDF is AGPL and is deliberately not pulled in by the OCR extras, ADR-0003)"
        )
        raise BackendError(msg) from exc
    return cast("PdfDocument", pymupdf.open(stream=payload, filetype="pdf"))


def optional_module(module_name: str, *, extra: str) -> ModuleType:
    """Import an optional dependency on demand, or fail with the public typed error."""
    try:
        return importlib.import_module(module_name)
    except ImportError as exc:
        raise DependencyUnavailableError(exc.name or module_name, extra) from exc


def _resolve_window(
    request: ConversionRequest,
    page_count: int,
) -> tuple[int, int, PageRange | None]:
    """Resolve the requested window against the document; ``None`` means out of bounds."""
    if request.page_range is None:
        return 1, page_count, PageRange(start=1, end=page_count)
    first = request.page_range.start
    last = min(request.page_range.end, page_count)
    if first > page_count:
        return first, last, None
    return first, last, PageRange(start=first, end=last)


def _failure(
    *,
    code: FailureCode,
    backend: str,
    backend_version: str,
    budget_s: float,
    started: float,
    detail: str,
    page_range: PageRange | None = None,
) -> PassFailure:
    """One typed failure record (ADR-0001 §7) for the processing trace."""
    return PassFailure(
        code=code,
        pass_kind=PassKind.VISUAL,
        page_range=page_range,
        backend=backend,
        backend_version=backend_version,
        budget_s=budget_s,
        elapsed_s=time.monotonic() - started,
        detail=detail,
        occurred_at=utcnow(),
    )


def _render_page(backend_name: str, page_number: int, text: str) -> PageResult:
    """One converted page: a single paragraph chunk, ids globally unique."""
    return PageResult(
        page_number=page_number,
        blocks=[
            StructuredChunk(
                id=f"{backend_name}-p{page_number}-b0",
                kind=ChunkKind.PARAGRAPH,
                content=text,
                page_number=page_number,
                reading_order=0,
                metadata={"backend": backend_name},
            )
        ],
    )
