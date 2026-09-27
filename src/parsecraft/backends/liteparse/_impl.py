"""Heavy LiteParse implementation — ``liteparse`` imported at top level.

Loaded only from the light factory at instantiation (``importlib``-based,
never an inline import). Verified against liteparse 2.14.7 (2026-09-27).

Design notes:
- ``ocr_enabled=False`` on purpose: scanned/image pages are routed to the
  OCR family by the planner (``is_complex`` signals say so), and this keeps
  the backend deterministic with no Tesseract dependency.
- Conversion parses **one page per bound-checked step** (``target_pages``), so
  cancellation, the timeout deadline, and the output budget are enforced
  between pages and a failing page becomes a typed ``BACKEND_ERROR`` record
  for that page's range instead of aborting the document. A single page's
  parse is one blocking call — the deadline cannot interrupt it mid-parse.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from time import monotonic

from liteparse import LiteParse  # ty: ignore[unresolved-import] — extra not installed in dev/CI; heavy by contract

from parsecraft.backends.errors import BackendError
from parsecraft.backends.liteparse.liteparse import DESCRIPTOR, LITEPARSE_BACKEND_VERSION
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

#: Shared parser settings: local, quiet, no internal OCR (see module docstring).
_PARSER_KWARGS: dict[str, object] = {
    "quiet": True,
    "ocr_enabled": False,
    "output_format": "markdown",
}


class LiteparseBackend:
    """Instantiated LiteParse backend: cheap ``is_complex`` analyze + paged convert."""

    name = DESCRIPTOR.name
    capabilities = DESCRIPTOR.capabilities

    def __init__(self, config: BackendConfig) -> None:
        self._config = config

    def convert(self, request: ConversionRequest) -> BackendResult:
        """Bound-checked, per-page conversion into typed IR (never raises per page)."""
        started = monotonic()
        reference = BackendRef(name=self.name, version=LITEPARSE_BACKEND_VERSION)
        failures: list[PassFailure] = []
        if request.cancellation is not None and request.cancellation():
            failures.append(_failure(request, started, FailureCode.CANCELLED, "cancelled before extraction", page_range=None))
            return _result(reference, [], failures, started)
        data = source_bytes(request.source)
        try:
            page_count = len(_parser().is_complex(data))
        except Exception as exc:
            failures.append(
                _failure(
                    request,
                    started,
                    FailureCode.INVALID_INPUT,
                    f"liteparse cannot read source {request.source.uri!r}: {type(exc).__name__}: {exc}",
                    page_range=None,
                )
            )
            return _result(reference, [], failures, started)
        window = _resolve_window(request.page_range, page_count)
        if window is None:
            failures.append(
                _failure(
                    request,
                    started,
                    FailureCode.INVALID_INPUT,
                    "requested page range is outside the document" if page_count else "source has no pages to convert",
                    page_range=request.page_range,
                )
            )
            return _result(reference, [], failures, started)
        first, last = window
        deadline = started + request.timeout_s if request.timeout_s is not None else None
        kept: list[PageResult] = []
        used_chars = 0
        for number in range(first, last + 1):
            if request.cancellation is not None and request.cancellation():
                failures.append(
                    _failure(
                        request,
                        started,
                        FailureCode.CANCELLED,
                        f"cancelled before page {number}",
                        page_range=PageRange(start=first, end=last),
                    )
                )
                break
            if deadline is not None and monotonic() >= deadline:
                failures.append(
                    _failure(
                        request,
                        started,
                        FailureCode.TIMEOUT,
                        f"time budget exhausted at page {number}",
                        page_range=PageRange(start=first, end=last),
                    )
                )
                break
            try:
                content = _parse_page(data, number)
            except Exception as exc:  # per-page failures are data, never raised
                failures.append(
                    _failure(
                        request,
                        started,
                        FailureCode.BACKEND_ERROR,
                        f"page {number}: {type(exc).__name__}: {exc}",
                        page_range=PageRange(start=number, end=number),
                    )
                )
                continue
            used_chars += len(content)
            if request.max_output_chars is not None and used_chars > request.max_output_chars:
                failures.append(
                    _failure(
                        request,
                        started,
                        FailureCode.BUDGET_EXCEEDED,
                        f"output budget of {request.max_output_chars} chars exceeded at page {number}",
                        page_range=PageRange(start=first, end=last),
                    )
                )
                break
            kept.append(_page_result(number, content))
        return _result(reference, kept, failures, started)

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        """Cheap text-layer pass: real per-page text/image/OCR signals, no conversion."""
        data = source_bytes(source)
        try:
            stats = _parser().is_complex(data)
        except Exception as exc:  # unsupported/undecodable input → typed, never raw
            msg = f"liteparse cannot inspect source {source.uri!r}: {type(exc).__name__}: {exc}"
            raise BackendError(msg) from exc
        signals = [
            PageSignal(
                page_number=entry.page_number,
                has_native_text=not entry.needs_ocr,
                text_chars=entry.text_length,
                image_count=entry.image_block_count,
                blank=entry.text_length == 0,
                replacement_char_ratio=1.0 if entry.is_garbled else None,
            )
            for entry in stats
        ]
        return AnalysisResult(
            source_hash=hashlib.sha256(data).hexdigest(),
            page_count=len(stats),
            signals=signals,
        )


def create(config: BackendConfig) -> DocumentBackend:
    """Provider entry for the light factory — the sanctioned heavy boundary."""
    return LiteparseBackend(config)


def _parse_page(data: bytes, page_number: int) -> str:
    """Markdown text of exactly one page (falls back to the plain text field)."""
    result = _parser(target_pages=str(page_number)).parse(data)
    if not result.pages:
        msg = f"liteparse returned no content for page {page_number}"
        raise RuntimeError(msg)
    page = result.pages[0]
    rendered = page.markdown or page.text or ""
    if not isinstance(rendered, str):  # defensive: wire fields are str | None
        msg = f"unexpected page content type: {type(rendered).__name__}"
        raise TypeError(msg)
    return rendered


def _parser(**overrides: object) -> LiteParse:
    """A fresh quiet parser (construction is config-only; the work is in parse())."""
    return LiteParse(**{**_PARSER_KWARGS, **overrides})


def _page_result(page_number: int, content: str) -> PageResult:
    """One converted page: a single paragraph chunk, ids globally unique."""
    return PageResult(
        page_number=page_number,
        blocks=[
            StructuredChunk(
                id=f"liteparse-p{page_number}-b0",
                kind=ChunkKind.PARAGRAPH,
                content=content,
                page_number=page_number,
                reading_order=0,
                metadata={"backend": "liteparse"},
            )
        ],
    )


def _resolve_window(page_range: PageRange | None, page_count: int) -> tuple[int, int] | None:
    """Resolve the requested window against the document; ``None`` means invalid."""
    if page_count == 0:
        return None
    if page_range is None:
        return 1, page_count
    if page_range.start > page_count:
        return None
    return page_range.start, min(page_range.end, page_count)


def source_bytes(source: SourceDocument) -> bytes:
    """Raw payload: in-memory ``content``, else a local ``file://`` read."""
    if source.content is not None:
        return source.content
    if "://" in source.uri and not source.uri.startswith("file://"):
        msg = f"source {source.uri!r} must carry in-memory content (only file:// URIs are read from disk)"
        raise BackendError(msg)
    path = Path(source.uri.removeprefix("file://"))
    try:
        return path.read_bytes()
    except OSError as exc:
        msg = f"cannot read source {source.uri!r}: {exc}"
        raise BackendError(msg) from exc


def _failure(
    request: ConversionRequest,
    started: float,
    code: FailureCode,
    detail: str,
    *,
    page_range: PageRange | None,
) -> PassFailure:
    """One typed failure record (ADR-0001 §7) for the processing trace."""
    return PassFailure(
        code=code,
        pass_kind=PassKind.NATIVE,
        page_range=page_range,
        backend="liteparse",
        backend_version=LITEPARSE_BACKEND_VERSION,
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
