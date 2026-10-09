"""Heavy half of the trafilatura backend — imported only by the factory body.

Behaviour contract (verbatim): ``extract`` runs with
``output_format="markdown", include_tables=True, include_links=False,
no_fallback=False``. An empty Markdown result falls back to plain-text
extraction before it is called a failure. The Markdown is projected into typed
chunks through the shared ``adapters.markdown.markdown_blocks`` parser, so
headings, lists, tables and code fences land in the IR as typed blocks rather
than one undifferentiated paragraph.

``analyze`` is deliberately *not* an analyzer: trafilatura builds a DOM tree to
extract content, so there is no cheaper structural signal than doing the work —
the honest analyzer stays the native text/HTML analyzer.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from time import monotonic
from typing import cast

import trafilatura  # ty: ignore[unresolved-import] — extra not installed in dev/CI; heavy by contract

from parsecraft.adapters import markdown_blocks
from parsecraft.backends.errors import BackendError
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
from parsecraft.backends.trafilatura.trafilatura import DESCRIPTOR, TRAFILATURA_BACKEND_VERSION
from parsecraft.ir.models import FailureCode, PageResult, PageSignal, PassFailure, PassKind, utcnow

_SUPPORTED = frozenset(DESCRIPTOR.capabilities.supported_formats)


class TrafilaturaBackend:
    """Instantiated trafilatura backend: HTML → one page of typed chunks."""

    name = DESCRIPTOR.name
    capabilities = DESCRIPTOR.capabilities

    def __init__(self, config: BackendConfig) -> None:
        self._config = config

    def convert(self, request: ConversionRequest) -> BackendResult:
        """Convert HTML into one page of typed Markdown chunks (never raises)."""
        started = monotonic()
        reference = BackendRef(name=self.name, version=TRAFILATURA_BACKEND_VERSION)
        if request.cancellation is not None and request.cancellation():
            return _result(reference, [], [_failure(request, started, FailureCode.CANCELLED, "cancelled before conversion")], started)
        if request.source.media_type not in _SUPPORTED:
            detail = f"unsupported media type: {request.source.media_type}"
            return _result(reference, [], [_failure(request, started, FailureCode.INVALID_INPUT, detail)], started)
        data = source_bytes(request.source)
        if not data.strip():
            return _result(reference, [], [_failure(request, started, FailureCode.INVALID_INPUT, "HTML conversion got no content")], started)
        try:
            # bytes, not str: trafilatura reads the declared charset itself, so a
            # non-UTF-8 page keeps its accents. Pre-decoding with errors="replace"
            # destroyed them (verified: latin-1 "café" came back "caf�").
            markdown = _extract(data)
        except Exception as exc:  # trafilatura parse boundary — typed, never raw
            detail = f"trafilatura conversion failed: {type(exc).__name__}: {exc}"
            return _result(reference, [], [_failure(request, started, FailureCode.BACKEND_ERROR, detail)], started)
        if not markdown or not markdown.strip():
            return _result(reference, [], [_failure(request, started, FailureCode.BACKEND_ERROR, "trafilatura returned no content")], started)
        pages = [PageResult(page_number=1, blocks=markdown_blocks(markdown))]
        return _result(reference, pages, [], started)

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        """Minimal structural signal — this backend is a converter, not an analyzer."""
        data = source_bytes(source)
        text = data.decode("utf-8", errors="replace")
        stripped = text.strip()
        return AnalysisResult(
            source_hash=hashlib.sha256(data).hexdigest(),
            page_count=1,
            signals=[
                PageSignal(
                    page_number=1,
                    has_native_text=bool(stripped),
                    text_chars=len(text),
                    image_count=0,
                    blank=not stripped,
                )
            ],
        )


def _extract(html: str | bytes) -> str:
    """Run trafilatura; an empty Markdown result falls back to plain text."""
    markdown = cast(
        "str",
        trafilatura.extract(
            html,
            output_format="markdown",
            include_tables=True,
            include_links=False,
            no_fallback=False,
        ),
    )
    if markdown and markdown.strip():
        return markdown
    # knox fallback: Markdown came back empty — try plain text before failing
    return cast("str", trafilatura.extract(html, output_format="text"))


def create(config: BackendConfig) -> DocumentBackend:
    """Instantiate the backend — the sanctioned heavy-import boundary."""
    return TrafilaturaBackend(config)


def _failure(request: ConversionRequest, started: float, code: FailureCode, detail: str) -> PassFailure:
    return PassFailure(
        code=code,
        pass_kind=PassKind.NATIVE,
        page_range=request.page_range,
        backend=DESCRIPTOR.name,
        backend_version=TRAFILATURA_BACKEND_VERSION,
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
