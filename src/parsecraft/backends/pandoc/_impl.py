"""Heavy pandoc implementation — ``pypandoc`` imported at top level.

Loaded only from the light factory at instantiation (``importlib``-based,
never an inline import). Verified against the **pandoc 3.11 binary** on
2026-09-28 — see ``pandoc.py`` for the round-trip table and the licence
gate (ADR-0005: the binary is GPL-2.0-or-later; the MIT wrapper ships in
the optional ``pandoc`` extra only).

Design notes:
- **Single logical page.** Pandoc has no layout/pagination: the result is
  one ``PageResult``; a ``page_range`` starting beyond page 1 is a typed
  ``INVALID_INPUT`` record, and multi-page capabilities stay off.
- **Binary check at instantiation:** ``create()`` asks pypandoc for the
  pandoc version once, so a missing system binary surfaces as a typed
  ``DependencyUnavailableError`` at ``registry.create`` — not mid-convert.
- **Bounds:** cancellation and the timeout deadline are checked before the
  (single, blocking) conversion; the output budget is checked after it. A
  failing conversion is a typed ``BACKEND_ERROR`` record, never a raise.
- **``analyze()`` is a size proxy by design:** office containers are
  zipped XML, so plain text cannot be counted cheaply; ``text_chars`` is
  the byte length (always ≥ the native-text threshold for a real document,
  so routing stays native and never escalates a text-bearing format to
  OCR). Documented, not silent.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from time import monotonic

import pypandoc  # ty: ignore[unresolved-import] — extra not installed in dev/CI; heavy by contract

from parsecraft.backends.errors import BackendError, DependencyUnavailableError
from parsecraft.backends.pandoc.pandoc import DESCRIPTOR, PANDOC_BACKEND_VERSION, PANDOC_READERS
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

#: Output projection: pandoc's GitHub-flavoured Markdown (deterministic).
_OUTPUT_FORMAT = "gfm"


class PandocBackend:
    """Instantiated pandoc backend: cheap analyze + single-pass conversion."""

    name = DESCRIPTOR.name
    capabilities = DESCRIPTOR.capabilities

    def __init__(self, config: BackendConfig) -> None:
        self._config = config
        try:
            pypandoc.get_pandoc_version()
        except (OSError, RuntimeError) as exc:
            # Missing system binary (GPL, user-installed — ADR-0005).
            raise DependencyUnavailableError("pandoc", "pandoc") from exc

    def convert(self, request: ConversionRequest) -> BackendResult:
        """Single-page conversion into typed IR (failures are records, never raises)."""
        started = monotonic()
        reference = BackendRef(name=self.name, version=PANDOC_BACKEND_VERSION)
        if request.cancellation is not None and request.cancellation():
            failure = _failure(request, started, FailureCode.CANCELLED, "cancelled before extraction", page_range=None)
            return _result(reference, [], [failure], started)
        data = source_bytes(request.source)
        media_type = request.source.media_type or ""
        reader = PANDOC_READERS.get(media_type)
        if reader is None:
            failure = _failure(
                request,
                started,
                FailureCode.INVALID_INPUT,
                f"pandoc has no verified reader for media type {media_type!r}",
                page_range=None,
            )
            return _result(reference, [], [failure], started)
        failure = _window_failure(request, started)
        if failure is not None:
            return _result(reference, [], [failure], started)
        try:
            markdown = pypandoc.convert_text(data, to=_OUTPUT_FORMAT, format=reader)
        except (OSError, RuntimeError) as exc:
            failure = _failure(
                request,
                started,
                FailureCode.BACKEND_ERROR,
                f"pandoc cannot convert {request.source.uri!r}: {type(exc).__name__}: {exc}",
                page_range=PageRange(start=1, end=1),
            )
            return _result(reference, [], [failure], started)
        if request.max_output_chars is not None and len(markdown) > request.max_output_chars:
            failure = _failure(
                request,
                started,
                FailureCode.BUDGET_EXCEEDED,
                f"output budget of {request.max_output_chars} chars exceeded ({len(markdown)} produced)",
                page_range=PageRange(start=1, end=1),
            )
            return _result(reference, [], [failure], started)
        return _result(reference, [_page_result(markdown)], [], started)

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        """Cheap size-proxy pass: one page, text-bearing format, no conversion."""
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


def _window_failure(request: ConversionRequest, started: float) -> PassFailure | None:
    """Range and deadline checks before the blocking conversion (None = proceed)."""
    if request.page_range is not None and request.page_range.start > 1:
        return _failure(
            request,
            started,
            FailureCode.INVALID_INPUT,
            "requested page range is outside the document (pandoc sources are single-page)",
            page_range=request.page_range,
        )
    deadline = started + request.timeout_s if request.timeout_s is not None else None
    if deadline is not None and monotonic() >= deadline:
        return _failure(request, started, FailureCode.TIMEOUT, "time budget exhausted before conversion", page_range=PageRange(start=1, end=1))
    return None


def create(config: BackendConfig) -> DocumentBackend:
    """Provider entry for the light factory — the sanctioned heavy boundary."""
    return PandocBackend(config)


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


def _page_result(content: str) -> PageResult:
    """The single converted page: one paragraph chunk, unique id."""
    return PageResult(
        page_number=1,
        blocks=[
            StructuredChunk(
                id="pandoc-p1-b0",
                kind=ChunkKind.PARAGRAPH,
                content=content,
                page_number=1,
                reading_order=0,
                metadata={"backend": "pandoc"},
            )
        ],
    )


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
        backend="pandoc",
        backend_version=PANDOC_BACKEND_VERSION,
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
