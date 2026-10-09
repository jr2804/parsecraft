"""Heavy MinerU implementation — ``mineru`` imported at top level.

Loaded only from the light factory at instantiation (``importlib``-based, never
an inline import). Conversion-verified against mineru 4.0.11 on 2026-10-09 for
PDF: an 80-page text PDF at ``effort="flash"`` produced 1382 content-list items
in 21.6 s with ``page_idx`` 0-based.

Design notes:
- ``analyze()`` stays cheap: PDFs are probed with ``pypdfium2`` (a ``docvortex``
  dependency, therefore present with this extra) — no models load and nothing
  is downloaded.
- ``convert()`` runs ONE ``doc_analyze`` pass at ``effort="flash"`` /
  ``parse_mode="auto"``. On a text PDF that path loads no weights at all
  (measured: ``$MINERU_HOME/models`` stays empty), so the default is CPU-only
  and weight-free. Scanned pages and higher efforts load the VLM checkpoint
  (see the licence statement in ``mineru.py``) and want a GPU; the executor's
  timeout deadline bounds that case.
- ``page_range`` is honoured by POST-FILTERING, never passed downstream:
  mineru's own page-range base is unverified (ADR-0007), so the adapter
  converts and then keeps only the requested pages. ``page_index_map`` exists
  upstream as a cheaper selection input but is deliberately unused until its
  base is verified.
- MinerU downloads its own weights into ``$MINERU_HOME/models``; ``create()``
  points ``MINERU_HOME`` at a revision directory of the managed cache (via
  ``assets.manager.default_cache_dir`` + ``model_revision_dir``) unless the user
  already set it, so ``models list``/``clean``/``remove`` see and reclaim the
  weights (ADR-0007 d4) and the offline carve-out applies.
- ``image_analysis`` defaults to off: the declared surface needs text chunks,
  and skipping the visual-crop stage is both faster (21.6 s vs 30.5 s on the
  verified fixture) and avoids rasterizing every page.
"""

from __future__ import annotations

import hashlib
import os
from html.parser import HTMLParser
from pathlib import Path
from time import monotonic
from typing import Any

import pypdfium2 as pdfium  # ty: ignore[unresolved-import] — docvortex dependency; heavy by contract
from mineru.backend.analyze import doc_analyze  # ty: ignore[unresolved-import] — extra not installed in dev/CI
from mineru.render import render_content_list  # ty: ignore[unresolved-import] — extra not installed in dev/CI

from parsecraft.assets.manager import default_cache_dir, model_revision_dir
from parsecraft.backends.errors import BackendError
from parsecraft.backends.mineru.mineru import (
    DESCRIPTOR,
    MINERU_ASSET,
    MINERU_BACKEND_VERSION,
    MINERU_NAME,
)
from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendConfig,
    BackendRef,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
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

#: Content List V1 ``type`` -> IR chunk kind. Page furniture maps to the IR's
#: own furniture kinds; ``page_number`` is dropped (see ``_map_item``).
_ITEM_KINDS: dict[str, ChunkKind] = {
    "text": ChunkKind.PARAGRAPH,
    "list": ChunkKind.LIST,
    "index": ChunkKind.LIST,
    "table": ChunkKind.TABLE,
    "equation": ChunkKind.FORMULA,
    "image": ChunkKind.CAPTION,
    "header": ChunkKind.HEADER,
    "footer": ChunkKind.FOOTER,
}

_MINERU_MEDIA_TYPE = "application/pdf"
_DEFAULT_EFFORT = "flash"
_EFFORTS = ("flash", "medium", "high", "xhigh")


class MineruBackend:
    """Instantiated MinerU backend: cheap structural analyze + paged convert."""

    name = DESCRIPTOR.name
    capabilities = DESCRIPTOR.capabilities

    def __init__(self, config: BackendConfig) -> None:
        self._config = config
        self._effort = _effort(config)
        self._image_analysis = _image_analysis(config)

    def convert(self, request: ConversionRequest) -> BackendResult:
        """Bound-checked conversion into typed IR (never raises)."""
        started = monotonic()
        reference = BackendRef(
            name=self.name,
            version=MINERU_BACKEND_VERSION,
            model_id=DESCRIPTOR.capabilities.model_asset.model_id if DESCRIPTOR.capabilities.model_asset else None,
            model_revision=DESCRIPTOR.capabilities.model_asset.model_revision if DESCRIPTOR.capabilities.model_asset else None,
        )
        if request.cancellation is not None and request.cancellation():
            return _result(reference, [], [_failure(request, started, FailureCode.CANCELLED, "cancelled before conversion")], started)
        configure_mineru_env()
        data = source_bytes(request.source)
        try:
            middle_json, _model_json = doc_analyze(
                data,
                effort=self._effort,
                parse_mode="auto",
                image_analysis=self._image_analysis,
                file_suffix="pdf",
            )
        except Exception as exc:  # mineru load/parse boundary — typed, never raw
            detail = f"mineru conversion failed: {type(exc).__name__}: {exc}"
            return _result(reference, [], [_failure(request, started, FailureCode.BACKEND_ERROR, detail)], started)
        if request.timeout_s is not None and monotonic() - started > request.timeout_s:
            return _result(reference, [], [_failure(request, started, FailureCode.TIMEOUT, "conversion exceeded timeout_s")], started)
        pages, failure = _select(request, middle_json, started)
        failures = [] if failure is None else [failure]
        return _result(reference, pages, failures, started)

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        """Cheap per-page signals: page count + native text length, no models."""
        data = source_bytes(source)
        signals = _pdf_signals(data)
        return AnalysisResult(
            source_hash=hashlib.sha256(data).hexdigest(),
            page_count=len(signals),
            signals=signals,
        )


class _TableTextExtractor(HTMLParser):
    """Flatten an HTML table body into ``cell | cell`` rows (stdlib only).

    MinerU hands tables over as HTML; the IR wants text it can project to
    Markdown, so tags become separators rather than surviving as markup.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._rows: list[list[str]] = []
        self._row: list[str] = []
        self._cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"}:
            self._cell = []

    def handle_data(self, data: str) -> None:
        self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"}:
            text = " ".join("".join(self._cell).split())
            if text:
                self._row.append(text)
            self._cell = []
        elif tag == "tr" and self._row:
            self._rows.append(self._row)

    def render(self) -> str:
        return "\n".join(" | ".join(row) for row in self._rows)


def create(config: BackendConfig) -> DocumentBackend:
    """Instantiate the backend — the sanctioned heavy-import boundary."""
    configure_mineru_env()
    return MineruBackend(config)


def configure_mineru_env() -> None:
    """Point MinerU's weight store inside the managed cache layout (ADR-0007 d4).

    MinerU fetches its own weights into ``$MINERU_HOME/models`` at conversion
    time. Without this they land in ``~/.mineru``, where ``parsecraft models
    list`` cannot see them and ``models clean``/``remove`` cannot reclaim them.
    The path is a revision directory of the managed layout, so the cache
    tooling reads and reclaims it like any other model. An explicitly set
    ``MINERU_HOME`` always wins — that is the user's own cache choice.
    """
    if os.environ.get("MINERU_HOME"):
        return
    asset = MINERU_ASSET
    os.environ["MINERU_HOME"] = str(model_revision_dir(default_cache_dir(), asset.model_id, asset.model_revision))


def _effort(config: BackendConfig) -> str:
    """Validate the pluggable ``effort`` option; defaults to the verified ``flash``."""
    raw = config.options.get("effort", _DEFAULT_EFFORT)
    if not isinstance(raw, str) or raw not in _EFFORTS:
        msg = f"unknown effort {raw!r}; expected one of: {', '.join(_EFFORTS)}"
        raise BackendError(msg)
    return raw


def _image_analysis(config: BackendConfig) -> bool:
    """Read the ``image_analysis`` option; defaults to off (see module docstring)."""
    raw = config.options.get("image_analysis", False)
    if not isinstance(raw, bool):
        msg = f"image_analysis option must be a boolean, got {type(raw).__name__}"
        raise BackendError(msg)
    return raw


def _pdf_signals(data: bytes) -> list[PageSignal]:
    """Per-page native text length via pypdfium2 (no models, no downloads)."""
    document = pdfium.PdfDocument(data)
    try:
        signals: list[PageSignal] = []
        for index in range(len(document)):
            page = document[index]
            textpage = page.get_textpage()
            try:
                text = textpage.get_text_range()
            finally:
                textpage.close()
                page.close()
            signals.append(
                PageSignal(
                    page_number=index + 1,
                    has_native_text=bool(text.strip()),
                    text_chars=len(text),
                    image_count=0,
                    blank=not text.strip(),
                )
            )
        return signals or [PageSignal(page_number=1, has_native_text=False, text_chars=0, image_count=0, blank=True)]
    finally:
        document.close()


def _select(
    request: ConversionRequest,
    middle_json: Any,
    started: float,
) -> tuple[list[PageResult], PassFailure | None]:
    """Map the content list to typed chunks per page, honoring bounds."""
    blocks: dict[int, list[StructuredChunk]] = {}
    used_chars = 0
    for item in render_content_list(middle_json):
        if request.cancellation is not None and request.cancellation():
            return _filled_pages(blocks, request.page_range), _failure(request, started, FailureCode.CANCELLED, "cancelled between items")
        kind, content = _map_item(item)
        if kind is None or not content:
            continue
        page_number = _page_number(item)
        if request.page_range is not None and not (request.page_range.start <= page_number <= request.page_range.end):
            continue
        if request.max_output_chars is not None and used_chars + len(content) > request.max_output_chars:
            return _filled_pages(blocks, request.page_range), _failure(request, started, FailureCode.BUDGET_EXCEEDED, "max_output_chars budget reached")
        used_chars += len(content)
        chunks = blocks.setdefault(page_number, [])
        chunks.append(
            StructuredChunk(
                id=f"mineru-{page_number}-b{len(chunks)}",
                kind=kind,
                content=content,
                page_number=page_number,
                reading_order=len(chunks),
            )
        )
    return _filled_pages(blocks, request.page_range), None


def _page_number(item: dict[str, Any]) -> int:
    """1-based IR page number for a content-list item.

    THE one place that normalizes the page index: MinerU's Content List V1 is
    0-based (``page_idx`` 0..N-1) while parsecraft IR is 1-based.
    """
    return int(item["page_idx"]) + 1


def _map_item(item: dict[str, Any]) -> tuple[ChunkKind | None, str]:
    """IR kind + text content for one content-list item.

    Items whose content is empty are dropped by the caller, which is how the
    verified ``flash`` path loses its equations: it emits them as images with
    no LaTeX text (at higher effort ``text``/``text_format`` are populated and
    the FORMULA chunk appears). Caption-less images are dropped the same way —
    image payloads are base64 data URIs and are never inlined into the IR.
    """
    item_type = str(item.get("type", ""))
    if item_type == "text":
        kind = ChunkKind.HEADING if item.get("text_level") is not None else ChunkKind.PARAGRAPH
        return kind, str(item.get("text", "")).strip()
    if item_type == "table":
        return ChunkKind.TABLE, _table_content(item)
    if item_type == "image":
        caption = _joined(item.get("image_caption"))
        return ChunkKind.CAPTION, caption
    kind = _ITEM_KINDS.get(item_type)
    if kind is None:
        return None, ""  # page_number and unknown types: page furniture
    if item_type in {"list", "index"}:
        return kind, _joined(item.get("list_items"))
    return kind, str(item.get("text", "")).strip()


def _table_content(item: dict[str, Any]) -> str:
    """Table caption + a plain-text rendering of the HTML ``table_body``."""
    extractor = _TableTextExtractor()
    extractor.feed(str(item.get("table_body") or ""))
    extractor.close()
    body = extractor.render()
    caption = _joined(item.get("table_caption"))
    if caption and body:
        return f"{caption}\n{body}"
    return caption or body


def _joined(value: Any) -> str:
    """Join MinerU's string-list fields (captions, footnotes, list items)."""
    if not isinstance(value, list):
        return ""
    return "\n".join(str(entry).strip() for entry in value if str(entry).strip())


def _filled_pages(blocks: dict[int, list[StructuredChunk]], page_range: PageRange | None) -> list[PageResult]:
    """Every requested page, empty when mineru produced no chunks for it."""
    numbers = sorted(blocks) or [1] if page_range is None else list(range(page_range.start, page_range.end + 1))
    return [PageResult(page_number=number, blocks=blocks.get(number, [])) for number in numbers]


def _failure(request: ConversionRequest, started: float, code: FailureCode, detail: str) -> PassFailure:
    return PassFailure(
        code=code,
        pass_kind=PassKind.NATIVE,
        page_range=request.page_range,
        backend=MINERU_NAME,
        backend_version=MINERU_BACKEND_VERSION,
        budget_s=request.timeout_s if request.timeout_s is not None else 0.0,
        elapsed_s=max(monotonic() - started, 0.0),
        detail=detail,
        occurred_at=utcnow(),
    )


def _result(reference: BackendRef, pages: list[PageResult], failures: list[PassFailure], started: float) -> BackendResult:
    return BackendResult(backend=reference, pages=pages, failures=failures, elapsed_s=max(monotonic() - started, 0.0))


def source_bytes(source: SourceDocument) -> bytes:
    """Raw bytes of a source document (``content`` or a local ``file://`` path)."""
    if source.content is not None:
        return source.content
    try:
        return _local_path(source.uri).read_bytes()
    except OSError as exc:
        msg = f"cannot read source {source.uri!r}: {exc}"
        raise BackendError(msg) from exc


def _local_path(uri: str) -> Path:
    """Filesystem path for a ``file://`` URI via the shared resolver."""
    return path_from_file_uri(uri)
