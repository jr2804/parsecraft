"""Canonical intermediate representation for ParseCraft.

Single source of truth for structured document data. Every backend converges
into these types; Markdown, HTML, JSON, and consumer projections render from
them. Rules live in ``AGENTS.md`` next to this file.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Version of the IR schema itself; bump on breaking changes.
SCHEMA_VERSION = "1"

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class AssetKind(StrEnum):
    """Kind of an extracted asset."""

    IMAGE = "image"
    LATEX = "latex"
    CSV = "csv"
    OTHER = "other"


# ── Enums ────────────────────────────────────────────────────────────────


class ChunkKind(StrEnum):
    """Kind of a structured chunk."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    FORMULA = "formula"
    CODE = "code"
    FIGURE = "figure"
    CAPTION = "caption"
    HEADER = "header"
    FOOTER = "footer"
    QUOTE = "quote"
    UNKNOWN = "unknown"


class DiagnosticLevel(StrEnum):
    """Severity of a diagnostic entry."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class FailureCode(StrEnum):
    """Machine-readable cause of a structured pass failure."""

    TIMEOUT = "timeout"
    BUDGET_EXCEEDED = "budget_exceeded"
    DEPENDENCY_MISSING = "dependency_missing"
    GPU_INSUFFICIENT = "gpu_insufficient"
    MODEL_UNAVAILABLE = "model_unavailable"
    CANCELLED = "cancelled"
    BACKEND_ERROR = "backend_error"
    INVALID_INPUT = "invalid_input"


class PassKind(StrEnum):
    """Processing pass that produced or judged a result."""

    NATIVE = "native"
    VISUAL = "visual"
    REPAIR = "repair"
    VERIFY = "verify"


class PassStatus(StrEnum):
    """Outcome of a processing pass."""

    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class RegionKind(StrEnum):
    """Kind of a detected page region."""

    TEXT = "text"
    TABLE = "table"
    FORMULA = "formula"
    FIGURE = "figure"
    HEADER = "header"
    FOOTER = "footer"


class RelationKind(StrEnum):
    """Kind of chunk or cross-page relation."""

    CONTINUATION = "continuation"
    CAPTION_OF = "caption_of"
    CROSS_REFERENCE = "cross_reference"


# ── Value objects ────────────────────────────────────────────────────────


class _ValueModel(BaseModel):
    """Base for immutable value objects."""

    model_config = ConfigDict(frozen=True)


class BBox(_ValueModel):
    """Axis-aligned bounding box in source coordinates (points)."""

    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def _ordered(self) -> BBox:
        if self.x1 < self.x0 or self.y1 < self.y0:
            msg = f"bbox corners must be ordered: {self}"
            raise ValueError(msg)
        return self


class SourceSpan(_ValueModel):
    """Character span in the original text source."""

    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> SourceSpan:
        if self.end < self.start:
            msg = f"span end must be >= start: {self.start} > {self.end}"
            raise ValueError(msg)
        return self


class QualitySignal(_ValueModel):
    """Named quality score in [0, 1] attached to a chunk or document."""

    name: str = Field(min_length=1)
    score: float = Field(ge=0, le=1)
    detail: str | None = None


class ChunkRelation(_ValueModel):
    """Relation from one chunk to another chunk id."""

    kind: RelationKind
    target_id: str = Field(min_length=1)


# ── Chunks ───────────────────────────────────────────────────────────────


class StructuredChunk(BaseModel):
    """One typed content unit — the package's currency.

    ``kind`` decides how :func:`parsecraft.ir.markdown.to_markdown` renders
    ``content``; ``metadata`` carries kind-specific hints (e.g.
    ``heading_level``, ``language``) as stringly-typed JSON-safe values.
    """

    id: str = Field(min_length=1)
    kind: ChunkKind
    content: str
    page_number: int = Field(ge=1)
    reading_order: int = Field(ge=0)
    bbox: BBox | None = None
    source_span: SourceSpan | None = None
    children: list[StructuredChunk] = Field(default_factory=list)
    relations: list[ChunkRelation] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    quality: list[QualitySignal] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)


class DetectedRegion(BaseModel):
    """A region detected on a page (layout signal, not yet content)."""

    id: str = Field(min_length=1)
    kind: RegionKind
    bbox: BBox
    confidence: float | None = Field(default=None, ge=0, le=1)


class ExtractedAsset(BaseModel):
    """An asset extracted from a page (image, LaTeX source, CSV, ...)."""

    id: str = Field(min_length=1)
    kind: AssetKind
    media_type: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    bbox: BBox | None = None
    sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)


class Diagnostic(BaseModel):
    """A recorded, non-fatal observation about a page."""

    level: DiagnosticLevel
    code: str = Field(min_length=1)
    message: str


class PageResult(BaseModel):
    """All extracted content of one page, in reading order."""

    page_number: int = Field(ge=1)
    width: float | None = Field(default=None, ge=0)
    height: float | None = Field(default=None, ge=0)
    blocks: list[StructuredChunk] = Field(default_factory=list)
    regions: list[DetectedRegion] = Field(default_factory=list)
    assets: list[ExtractedAsset] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)

    @model_validator(mode="after")
    def _blocks_consistent(self) -> PageResult:
        orders = [b.reading_order for b in self.blocks]
        if orders != sorted(orders):
            msg = f"page {self.page_number}: blocks must be in reading_order"
            raise ValueError(msg)
        wrong = [b.id for b in self.blocks if b.page_number != self.page_number]
        if wrong:
            msg = f"page {self.page_number}: blocks carry wrong page_number: {wrong}"
            raise ValueError(msg)
        return self


# ── Cross-page structure, trace, failures ────────────────────────────────


class PageRange(_ValueModel):
    """Inclusive 1-based page range."""

    start: int = Field(ge=1)
    end: int = Field(ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> PageRange:
        if self.end < self.start:
            msg = f"page range end must be >= start: {self.start} > {self.end}"
            raise ValueError(msg)
        return self


class CrossPageRelation(BaseModel):
    """Relation between content on different pages."""

    kind: RelationKind
    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    source_page: int = Field(ge=1)
    target_page: int = Field(ge=1)


class PassFailure(_ValueModel):
    """Structured failure record — typed, never a bare string."""

    code: FailureCode
    pass_kind: PassKind
    page_range: PageRange | None = None
    backend: str = Field(min_length=1)
    backend_version: str = Field(min_length=1)
    budget_s: float = Field(ge=0)
    elapsed_s: float = Field(ge=0)
    detail: str
    occurred_at: datetime


class TraceEntry(BaseModel):
    """One processing pass recorded in the document's trace."""

    pass_kind: PassKind
    backend: str = Field(min_length=1)
    backend_version: str = Field(min_length=1)
    status: PassStatus
    page_range: PageRange | None = None
    elapsed_s: float = Field(ge=0)
    settings: dict[str, str] = Field(default_factory=dict)
    failure: PassFailure | None = None

    @model_validator(mode="after")
    def _failure_matches_status(self) -> TraceEntry:
        if (self.status is PassStatus.FAILED) != (self.failure is not None):
            msg = "status 'failed' and failure record must appear together"
            raise ValueError(msg)
        return self


# ── Document ─────────────────────────────────────────────────────────────


class DocumentMetadata(BaseModel):
    """Document-level metadata and reproducibility anchors."""

    source_uri: str = Field(min_length=1)
    source_hash: str = Field(pattern=_SHA256_PATTERN)
    format: str = Field(min_length=1)
    page_count: int = Field(ge=0)
    title: str | None = None
    produced_at: datetime
    package_version: str = Field(min_length=1)
    schema_version: str = Field(default=SCHEMA_VERSION)


class DocumentResult(BaseModel):
    """Root of the canonical IR — everything projections need."""

    metadata: DocumentMetadata
    pages: list[PageResult] = Field(default_factory=list)
    relations: list[CrossPageRelation] = Field(default_factory=list)
    trace: list[TraceEntry] = Field(default_factory=list)
    quality: list[QualitySignal] = Field(default_factory=list)

    @model_validator(mode="after")
    def _document_consistent(self) -> DocumentResult:
        numbers = [p.page_number for p in self.pages]
        if numbers != sorted(set(numbers)):
            msg = f"pages must be unique and in ascending order, got {numbers}"
            raise ValueError(msg)
        if self.metadata.page_count != len(self.pages):
            msg = f"metadata.page_count {self.metadata.page_count} != {len(self.pages)} pages"
            raise ValueError(msg)
        seen: set[str] = set()
        duplicates: set[str] = set()

        def _collect(chunks: list[StructuredChunk]) -> None:
            for chunk in chunks:
                if chunk.id in seen:
                    duplicates.add(chunk.id)
                seen.add(chunk.id)
                _collect(chunk.children)

        for page in self.pages:
            _collect(page.blocks)
        if duplicates:
            msg = f"duplicate chunk ids: {sorted(duplicates)}"
            raise ValueError(msg)
        return self


def utcnow() -> datetime:
    """Timezone-aware now — single home for timestamps in IR construction."""
    return datetime.now(UTC)
