"""Heavy Marker implementation — ``marker`` imported at top level.

Loaded only from the light factory at instantiation (``importlib``-based,
never an inline import). Implements the adapter contract from ADR-0006
decision 3:

- ``supported_formats`` is ``["application/pdf"]`` only (see ``marker.py``).
- Marker is a **converter candidate, not an analyzer** — ``analyze()``
  provides a minimal one-page signal; Marker has no cheap detection API.
- Page numbers normalize 1-based IR ↔ marker's 0-based ``page`` in the
  adapter, one place: ``_split_pages`` maps 0 → 1, 1 → 2, etc.
- **No construction-time network** — the model-weight fetch
  (``create_model_dict``) is deferred to ``_converter()``; if it fails
  the backend records a typed ``DEPENDENCY_MISSING`` pass-failure instead of
  crashing or mutating site-packages behind the operator's back.

Conversion reads the source bytes, runs ``PdfConverter``, and maps
``text_from_rendered`` output into per-page ``PageResult`` IR. Pagination
markers (``{n}---…---`` with 0-based ``n``) are split and rebased to 1-based.
"""

from __future__ import annotations

import hashlib
import re
from io import BytesIO
from pathlib import Path
from time import monotonic

from marker.converters.pdf import PdfConverter  # ty: ignore[unresolved-import]
from marker.models import create_model_dict  # ty: ignore[unresolved-import]
from marker.output import text_from_rendered  # ty: ignore[unresolved-import]

from parsecraft.backends.errors import (
    BackendError,
)
from parsecraft.backends.marker.marker import DESCRIPTOR, MARKER_BACKEND_VERSION, MARKER_FORMATS
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

#: Default marker page separator (``"-" * 48`` per marker.renderers.markdown).
_PAGE_SEPARATOR = "-" * 48

#: Matches marker's pagination fence: ``\n\n{0}----…----\n\n``.
_PAGE_MARKER_RE = re.compile(r"\n\n\{\d+\}\n" + re.escape(_PAGE_SEPARATOR) + r"\n\n")

#: Process-wide converter (one per process is the marker contract).
_CONVERTER: PdfConverter | None = None


class MarkerBackend:
    """Instantiated Marker backend: minimal analyze + paged convert."""

    name = DESCRIPTOR.name
    capabilities = DESCRIPTOR.capabilities

    def __init__(self, config: BackendConfig) -> None:
        self._config = config

    def convert(self, request: ConversionRequest) -> BackendResult:
        """Convert a PDF into typed IR pages.

        A source this backend does not ingest is a typed
        ``FailureCode.INVALID_INPUT`` pass-failure record — the same shape the
        rest of the family uses, and unreachable through the planner, which only
        routes formats the descriptor declares. Non-format failures stay typed
        records, never raises.
        """
        started = monotonic()
        reference = BackendRef(name=self.name, version=MARKER_BACKEND_VERSION)

        preflight = _preflight_failure(request, started)
        if preflight is not None:
            return _result(reference, [], [preflight], started)

        data = source_bytes(request.source)

        # ── Converter (may need model weights — deferred, never at init) ──
        try:
            converter = _converter()
        except (ImportError, OSError, RuntimeError) as exc:
            return _result(
                reference,
                [],
                [_failure(request, started, FailureCode.DEPENDENCY_MISSING, f"marker models unavailable: {type(exc).__name__}: {exc}")],
                started,
            )

        deadline = started + request.timeout_s if request.timeout_s is not None else None
        if deadline is not None and monotonic() >= deadline:
            return _result(
                reference,
                [],
                [_failure(request, started, FailureCode.TIMEOUT, "time budget exhausted before conversion")],
                started,
            )

        try:
            rendered = converter(BytesIO(data))
            text, _ext, _images = text_from_rendered(rendered)
        except Exception as exc:
            return _result(
                reference,
                [],
                [_failure(request, started, FailureCode.BACKEND_ERROR, f"marker conversion failed: {type(exc).__name__}: {exc}")],
                started,
            )

        page_count = getattr(converter, "page_count", 1) or 1
        page_texts = _split_pages(text, page_count)

        # ── Build per-page IR, honoring range + output budget ──
        pages: list[PageResult] = []
        used_chars = 0
        start_page = request.page_range.start if request.page_range is not None else 1
        end_page = request.page_range.end if request.page_range is not None else page_count

        for page_number in range(1, page_count + 1):
            if page_number < start_page or page_number > end_page:
                continue
            content = page_texts[page_number - 1] if page_number - 1 < len(page_texts) else ""
            guard = _page_failure(request, started, page_number, start_page, end_page, deadline, used_chars + len(content))
            if guard is not None:
                return _result(reference, pages, [guard], started)
            used_chars += len(content)
            pages.append(_page_result(page_number, content))

        if not pages:
            # All requested pages are out of range — return one empty page
            # so the executor's page-count contract holds.
            pages = [PageResult(page_number=start_page, blocks=[])]

        return _result(reference, pages, [], started)

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        """Cheap analysis: minimal signal, no Marker models (ADR-0006).

        Marker has no cheap detection API, so analyze() provides a one-page
        size proxy — byte length as ``text_chars`` (always ≥ the native-text
        threshold for a real document, so routing stays native and never
        escalates to OCR). The converter handles actual page splitting.
        """
        data = source_bytes(source)
        return AnalysisResult(
            source_hash=hashlib.sha256(data).hexdigest(),
            page_count=1,
            signals=[
                PageSignal(
                    page_number=1,
                    has_native_text=bool(data),
                    text_chars=len(data),
                    image_count=0,
                    blank=not data,
                    replacement_char_ratio=None,
                )
            ],
        )


def create(config: BackendConfig) -> DocumentBackend:
    """Instantiate the backend — the sanctioned heavy-import boundary."""
    return MarkerBackend(config)


def _converter() -> PdfConverter:
    """Lazily build the process-wide ``PdfConverter``.

    ``create_model_dict()`` may fetch model weights and the 14 MB font
    (ADR-0006). If the fetch fails — network, offline, or restricted weights —
    the caller receives the exception and converts it into a typed
    ``DEPENDENCY_MISSING`` failure rather than a raw crash.
    """
    global _CONVERTER  # noqa: PLW0603 — one converter per process
    if _CONVERTER is None:
        _CONVERTER = PdfConverter(artifact_dict=create_model_dict())
    return _CONVERTER


def _split_pages(markdown: str, page_count: int) -> list[str]:
    r"""Split marker's paginated markdown into per-page text.

    Marker fences pages with ``\n\n{n}---…---\n\n`` where ``n`` is 0-based.
    This function rebases them to 0-based list indices (page 1 → index 0).
    When no page markers are present, the whole text is treated as one page.
    """
    if page_count <= 1:
        return [markdown.strip()]

    parts = _PAGE_MARKER_RE.split(markdown)
    if len(parts) <= 1:
        return [markdown.strip()]

    # split yields: [preamble, page0_text, page1_text, ...]
    pages = [text.strip() for text in parts[1:]]
    while len(pages) < page_count:
        pages.append("")
    return pages


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


def _page_result(page_number: int, content: str) -> PageResult:
    """One converted page: a single paragraph chunk, unique id."""
    return PageResult(
        page_number=page_number,
        blocks=[
            StructuredChunk(
                id=f"marker-p{page_number}-b0",
                kind=ChunkKind.PARAGRAPH,
                content=content,
                page_number=page_number,
                reading_order=0,
                metadata={"backend": "marker"},
            )
        ],
    )


def _preflight_failure(request: ConversionRequest, started: float) -> PassFailure | None:
    """Format and cancellation checks that run before any heavy work.

    An unsupported media type is a typed ``INVALID_INPUT`` record — the family's
    shape — and unreachable through the planner, which only routes formats the
    descriptor declares.
    """
    media_type = request.source.media_type or ""
    if media_type not in MARKER_FORMATS:
        return _failure(request, started, FailureCode.INVALID_INPUT, f"marker ingests {', '.join(MARKER_FORMATS)}; got {media_type!r}")
    if request.cancellation is not None and request.cancellation():
        return _failure(request, started, FailureCode.CANCELLED, "cancelled before conversion")
    return None


def _page_failure(
    request: ConversionRequest,
    started: float,
    page_number: int,
    start_page: int,
    end_page: int,
    deadline: float | None,
    projected_chars: int,
) -> PassFailure | None:
    """Cancel/timeout/output-budget check for one page, as a typed record."""
    page_range = PageRange(start=start_page, end=end_page)
    if request.cancellation is not None and request.cancellation():
        return _failure(request, started, FailureCode.CANCELLED, f"cancelled at page {page_number}", page_range=page_range)
    if deadline is not None and monotonic() >= deadline:
        return _failure(request, started, FailureCode.TIMEOUT, f"time budget exhausted at page {page_number}", page_range=page_range)
    if request.max_output_chars is not None and projected_chars > request.max_output_chars:
        detail = f"output budget of {request.max_output_chars} chars exceeded at page {page_number}"
        return _failure(request, started, FailureCode.BUDGET_EXCEEDED, detail, page_range=page_range)
    return None


def _failure(
    request: ConversionRequest,
    started: float,
    code: FailureCode,
    detail: str,
    *,
    page_range: PageRange | None = None,
) -> PassFailure:
    """One typed failure record for the processing trace."""
    return PassFailure(
        code=code,
        pass_kind=PassKind.NATIVE,
        page_range=page_range,
        backend="marker",
        backend_version=MARKER_BACKEND_VERSION,
        budget_s=request.timeout_s if request.timeout_s is not None else 0.0,
        elapsed_s=max(monotonic() - started, 0.0),
        detail=detail,
        occurred_at=utcnow(),
    )


def _result(
    reference: BackendRef,
    pages: list[PageResult],
    failures: list[PassFailure],
    started: float,
) -> BackendResult:
    return BackendResult(
        backend=reference,
        pages=pages,
        failures=failures,
        elapsed_s=max(monotonic() - started, 0.0),
    )
