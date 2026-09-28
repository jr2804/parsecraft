"""Shared machinery for the built-in native backend family.

``native-pdf`` splits responsibilities (ADR-0003): analysis/inspection uses
pypdf (``pdf-lite`` extra, permissive) while text extraction uses PyMuPDF
(``pdf`` extra, AGPL) — so ``analyze()`` works without the AGPL extra.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from time import monotonic

from pydantic import BaseModel, Field

from parsecraft.backends.errors import BackendError, DependencyUnavailableError
from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendCapabilities,
    BackendConfig,
    BackendRef,
    BackendResult,
    ConversionRequest,
    SourceDocument,
)
from parsecraft.ir.models import (
    ChunkKind,
    FailureCode,
    PageResult,
    PageSignal,
    PassFailure,
    PassKind,
    StructuredChunk,
    utcnow,
)

NATIVE_BACKEND_VERSION = "0.1.0"

_BLOCK_SPLIT = re.compile(r"\n[ \t]*\n+")

#: Builds all pages of a source document from its raw bytes.
Extract = Callable[[SourceDocument, bytes], list[PageResult]]
#: Counts visible images in raw document bytes (deterministic heuristic).
ImageCounter = Callable[[bytes], int]
#: Counts pages in raw document bytes (deterministic, no conversion).
PageCounter = Callable[[bytes], int]


class SourceReadError(BackendError):
    """The source document's bytes could not be read."""

    def __init__(self, uri: str) -> None:
        self.uri = uri
        super().__init__(f"cannot read source document: {uri!r}")


class PageTextStats(BaseModel):
    """Per-page text statistics gathered during PDF inspection."""

    page_number: int = Field(ge=1)
    text_chars: int = Field(ge=0)
    replacement_char_ratio: float | None = Field(default=None, ge=0, le=1)
    blank: bool


class PdfInspection(BaseModel):
    """Outcome of pypdf-based inspection (no content extraction)."""

    encrypted: bool
    readable: bool
    page_count: int = Field(ge=0)
    pages: list[PageTextStats] = Field(default_factory=list)


class NativeBackendBase:
    """Analyze/convert flow shared by all native backends.

    Subclasses inject their extraction, page-count, and image-count callables
    at construction; heavy optional imports happen inside those callables
    (``import_module`` boundary), never at module import.
    """

    name: str
    capabilities: BackendCapabilities
    backend_version = NATIVE_BACKEND_VERSION

    def __init__(
        self,
        config: BackendConfig,
        capabilities: BackendCapabilities,
        *,
        extract: Extract,
        page_count: PageCounter | None = None,
        count_images: ImageCounter | None = None,
    ) -> None:
        self._config = config
        self.capabilities = capabilities
        self._extract = extract
        self._page_count = page_count if page_count is not None else one_page
        self._count_images = count_images if count_images is not None else no_images

    def analyze(self, source: SourceDocument) -> AnalysisResult:
        data = source_bytes(source)
        blank = not data.strip()
        return AnalysisResult(
            source_hash=hashlib.sha256(data).hexdigest(),
            page_count=self._page_count(data),
            signals=[
                PageSignal(
                    page_number=1,
                    has_native_text=not blank,
                    text_chars=len(data),
                    image_count=self._count_images(data),
                    blank=blank,
                )
            ],
        )

    def convert(self, request: ConversionRequest) -> BackendResult:
        started = monotonic()
        ref = BackendRef(name=self.name, version=self.backend_version)
        if request.cancellation is not None and request.cancellation():
            return self._result(ref, request, [], started, code=FailureCode.CANCELLED, detail="cancelled before extraction")
        data = source_bytes(request.source)
        try:
            pages = self._extract(request.source, data)
        except DependencyUnavailableError as exc:
            return self._result(ref, request, [], started, code=FailureCode.DEPENDENCY_MISSING, detail=str(exc))
        if request.timeout_s is not None and monotonic() - started > request.timeout_s:
            return self._result(ref, request, [], started, code=FailureCode.TIMEOUT, detail="extraction exceeded timeout_s")
        kept, failure = self._select(request, pages, started)
        result = BackendResult(backend=ref, pages=kept, failures=[], elapsed_s=max(monotonic() - started, 0.0))
        if failure is not None:
            result.failures.append(failure)
        return result

    def _select(
        self,
        request: ConversionRequest,
        pages: list[PageResult],
        started: float,
    ) -> tuple[list[PageResult], PassFailure | None]:
        page_range = request.page_range
        candidates = pages if page_range is None else [page for page in pages if page_range.start <= page.page_number <= page_range.end]
        kept: list[PageResult] = []
        used_chars = 0
        for page in candidates:
            if request.cancellation is not None and request.cancellation():
                return kept, self._failure(request, started, code=FailureCode.CANCELLED, detail="cancelled between pages")
            page_chars = sum(len(block.content) for block in page.blocks)
            if request.max_output_chars is not None and used_chars + page_chars > request.max_output_chars:
                return kept, self._failure(request, started, code=FailureCode.BUDGET_EXCEEDED, detail="max_output_chars budget reached")
            used_chars += page_chars
            kept.append(page)
        return kept, None

    def _failure(
        self,
        request: ConversionRequest,
        started: float,
        *,
        code: FailureCode,
        detail: str,
    ) -> PassFailure:
        return PassFailure(
            code=code,
            pass_kind=PassKind.NATIVE,
            backend=self.name,
            backend_version=self.backend_version,
            budget_s=request.timeout_s if request.timeout_s is not None else 0.0,
            elapsed_s=max(monotonic() - started, 0.0),
            detail=detail,
            occurred_at=utcnow(),
        )

    def _result(
        self,
        ref: BackendRef,
        request: ConversionRequest,
        pages: list[PageResult],
        started: float,
        *,
        code: FailureCode,
        detail: str,
    ) -> BackendResult:
        return BackendResult(
            backend=ref,
            pages=pages,
            failures=[self._failure(request, started, code=code, detail=detail)],
            elapsed_s=max(monotonic() - started, 0.0),
        )


def no_images(data: bytes) -> int:
    """Default image counter: native text formats carry no images."""
    return 0


def one_page(data: bytes) -> int:
    """Default page counter: single-page text formats."""
    return 1


def source_bytes(source: SourceDocument) -> bytes:
    """Raw bytes of a source document (``content`` or a local ``file://`` path)."""
    if source.content is not None:
        return source.content
    path = Path(source.uri.removeprefix("file://"))
    try:
        return path.read_bytes()
    except OSError as exc:
        raise SourceReadError(source.uri) from exc


def paragraph_chunks(prefix: str, page_number: int, text: str) -> list[StructuredChunk]:
    """Typed paragraph chunks for one page's plain text."""
    return [
        StructuredChunk(
            id=f"{prefix}-{page_number}-b{index}",
            kind=ChunkKind.PARAGRAPH,
            content=block,
            page_number=page_number,
            reading_order=index,
        )
        for index, block in enumerate(split_paragraphs(text))
    ]


def split_paragraphs(text: str) -> list[str]:
    """Blank-line-separated blocks of non-whitespace content."""
    if not text.strip():
        return []
    return [block.strip() for block in _BLOCK_SPLIT.split(text.strip()) if block.strip()]
