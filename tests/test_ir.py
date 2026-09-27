"""Canonical IR: construction, invariants, structured failure records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from pydantic import ValidationError

from parsecraft.ir import (
    SCHEMA_VERSION,
    AssetKind,
    BBox,
    ChunkKind,
    ChunkRelation,
    CrossPageRelation,
    DetectedRegion,
    DocumentMetadata,
    DocumentResult,
    ExtractedAsset,
    FailureCode,
    PageRange,
    PageResult,
    PassFailure,
    PassKind,
    PassStatus,
    QualitySignal,
    RegionKind,
    RelationKind,
    SourceSpan,
    StructuredChunk,
    TraceEntry,
    utcnow,
)

_SHA = "a" * 64


def test_schema_version_is_pinned() -> None:
    assert SCHEMA_VERSION == "1"
    assert _metadata().schema_version == "1"


def test_document_result_happy_path() -> None:
    result = DocumentResult(
        metadata=_metadata(page_count=2),
        pages=[
            PageResult(page_number=1, blocks=[_chunk("c1", 1, 0), _chunk("c2", 1, 1)]),
            PageResult(page_number=2, blocks=[_chunk("c3", 2, 0)]),
        ],
        relations=[
            CrossPageRelation(
                kind=RelationKind.CONTINUATION,
                source_id="c2",
                target_id="c3",
                source_page=1,
                target_page=2,
            )
        ],
        trace=[
            TraceEntry(
                pass_kind=PassKind.NATIVE,
                backend="pymupdf",
                backend_version="2.0",
                status=PassStatus.OK,
                elapsed_s=1.5,
            )
        ],
        quality=[QualitySignal(name="text_density", score=0.9)],
    )
    assert len(result.pages) == 2
    assert result.trace[0].status is PassStatus.OK


def test_nested_children_and_relations_are_typed() -> None:
    parent = _chunk("c1")
    parent.children = [_chunk("c-child")]
    parent.relations = [ChunkRelation(kind=RelationKind.CAPTION_OF, target_id="c2")]
    bbox = BBox(x0=0.0, y0=0.0, x1=10.0, y1=20.0)
    parent.bbox = bbox
    parent.source_span = SourceSpan(start=0, end=5)
    assert parent.children[0].id == "c-child"
    assert parent.bbox.x1 == 10.0


# ── Value-object invariants ──────────────────────────────────────────────


def test_bbox_rejects_inverted_corners() -> None:
    with pytest.raises(ValidationError, match="ordered"):
        BBox(x0=10.0, y0=0.0, x1=5.0, y1=20.0)


def test_source_span_rejects_reversed_span() -> None:
    with pytest.raises(ValidationError, match="end must be >= start"):
        SourceSpan(start=9, end=3)


def test_source_span_rejects_negative_offsets() -> None:
    with pytest.raises(ValidationError):
        SourceSpan(start=-1, end=5)


def test_page_range_rejects_reversed_range() -> None:
    with pytest.raises(ValidationError, match="end must be >= start"):
        PageRange(start=7, end=2)


def test_page_range_is_one_based() -> None:
    with pytest.raises(ValidationError):
        PageRange(start=0, end=3)


def test_quality_signal_bounds() -> None:
    with pytest.raises(ValidationError):
        QualitySignal(name="x", score=1.5)


def test_chunk_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        StructuredChunk(
            id="c1",
            kind=ChunkKind.PARAGRAPH,
            content="hello",
            page_number=1,
            reading_order=0,
            confidence=-0.1,
        )


def test_chunk_metadata_is_strictly_str_valued() -> None:
    # Deliberately invalid value type: pydantic must reject it at runtime.
    bad_metadata = cast("dict[str, str]", {"level": 2})
    with pytest.raises(ValidationError):
        StructuredChunk(
            id="c1",
            kind=ChunkKind.PARAGRAPH,
            content="hello",
            page_number=1,
            reading_order=0,
            metadata=bad_metadata,
        )


def test_chunk_ids_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        _chunk("")


# ── Page invariants ──────────────────────────────────────────────────────


def test_page_rejects_blocks_out_of_reading_order() -> None:
    with pytest.raises(ValidationError, match="reading_order"):
        PageResult(page_number=1, blocks=[_chunk("a", 1, 5), _chunk("b", 1, 1)])


def test_page_rejects_blocks_from_other_pages() -> None:
    with pytest.raises(ValidationError, match="wrong page_number"):
        PageResult(page_number=2, blocks=[_chunk("a", 1, 0)])


def test_page_accepts_empty_blocks() -> None:
    assert PageResult(page_number=1).blocks == []


# ── Document invariants ──────────────────────────────────────────────────


def test_document_rejects_unordered_pages() -> None:
    with pytest.raises(ValidationError, match="ascending order"):
        DocumentResult(
            metadata=_metadata(page_count=2),
            pages=[PageResult(page_number=2), PageResult(page_number=1)],
        )


def test_document_rejects_duplicate_pages() -> None:
    with pytest.raises(ValidationError, match="ascending order"):
        DocumentResult(
            metadata=_metadata(page_count=2),
            pages=[PageResult(page_number=1), PageResult(page_number=1)],
        )


def test_document_rejects_page_count_mismatch() -> None:
    with pytest.raises(ValidationError, match="page_count"):
        DocumentResult(metadata=_metadata(page_count=5), pages=[PageResult(page_number=1)])


def test_document_rejects_duplicate_chunk_ids_globally() -> None:
    nested = _chunk("dup", 1, 1)
    nested.children = [_chunk("inner", 1, 1)]
    other = _chunk("inner", 2, 0)
    with pytest.raises(ValidationError, match="duplicate chunk ids: \\['dup', 'inner'\\]"):
        DocumentResult(
            metadata=_metadata(page_count=2),
            pages=[
                PageResult(page_number=1, blocks=[_chunk("dup", 1, 0), nested]),
                PageResult(page_number=2, blocks=[other]),
            ],
        )


def test_document_accepts_empty_document() -> None:
    result = DocumentResult(metadata=_metadata(page_count=0))
    assert result.pages == []


def test_failed_status_requires_failure_record() -> None:
    with pytest.raises(ValidationError, match="must appear together"):
        TraceEntry(
            pass_kind=PassKind.NATIVE,
            backend="docling",
            backend_version="2.0",
            status=PassStatus.FAILED,
            elapsed_s=1.0,
        )


def test_failure_record_without_failed_status_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must appear together"):
        TraceEntry(
            pass_kind=PassKind.NATIVE,
            backend="docling",
            backend_version="2.0",
            status=PassStatus.OK,
            elapsed_s=1.0,
            failure=_failure(),
        )


def test_failed_status_with_failure_record_is_accepted() -> None:
    entry = TraceEntry(
        pass_kind=PassKind.NATIVE,
        backend="docling",
        backend_version="2.0",
        status=PassStatus.FAILED,
        elapsed_s=31.2,
        failure=_failure(),
    )
    assert entry.failure is not None
    assert entry.failure.code is FailureCode.BUDGET_EXCEEDED


def test_failure_record_rejects_negative_budget() -> None:
    with pytest.raises(ValidationError):
        PassFailure(
            code=FailureCode.BUDGET_EXCEEDED,
            pass_kind=PassKind.NATIVE,
            backend="docling",
            backend_version="2.0",
            budget_s=-1.0,
            elapsed_s=1.0,
            detail="x",
            occurred_at=datetime(2026, 1, 2, tzinfo=UTC),
        )


# ── Misc ─────────────────────────────────────────────────────────────────


def test_utcnow_is_aware() -> None:
    now = utcnow()
    assert now.tzinfo is not None
    assert now.utcoffset() is not None


def test_value_objects_are_frozen() -> None:
    bbox = BBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0)
    with pytest.raises(ValidationError):  # noqa: PT011 — pydantic raises ValidationError
        bbox.x0 = 5.0  # ty: ignore[invalid-assignment]


def test_source_span_is_frozen() -> None:
    span = SourceSpan(start=0, end=1)
    with pytest.raises(ValidationError):  # noqa: PT011 — pydantic raises ValidationError
        span.end = 5  # ty: ignore[invalid-assignment]


def test_page_range_is_frozen() -> None:
    page_range = PageRange(start=1, end=3)
    with pytest.raises(ValidationError):  # noqa: PT011 — pydantic raises ValidationError
        page_range.end = 9  # ty: ignore[invalid-assignment]


def test_pass_failure_is_frozen() -> None:
    failure = _failure()
    with pytest.raises(ValidationError):  # noqa: PT011 — pydantic raises ValidationError
        failure.detail = "rewritten"  # ty: ignore[invalid-assignment]


def test_quality_signal_is_frozen() -> None:
    signal = QualitySignal(name="density", score=0.5)
    with pytest.raises(ValidationError):  # noqa: PT011 — pydantic raises ValidationError
        signal.score = 1.0  # ty: ignore[invalid-assignment]


def test_chunk_relation_is_frozen() -> None:
    relation = ChunkRelation(kind=RelationKind.CAPTION_OF, target_id="c2")
    with pytest.raises(ValidationError):  # noqa: PT011 — pydantic raises ValidationError
        relation.target_id = "c3"  # ty: ignore[invalid-assignment]


def test_enum_members_round_trip() -> None:
    assert ChunkKind("heading") is ChunkKind.HEADING
    assert FailureCode("timeout") is FailureCode.TIMEOUT
    assert PassStatus("cancelled") is PassStatus.CANCELLED


def test_failure_code_values_are_pinned() -> None:
    assert set(FailureCode) == {
        "timeout",
        "budget_exceeded",
        "dependency_missing",
        "gpu_insufficient",
        "model_unavailable",
        "cancelled",
        "backend_error",
        "invalid_input",
    }


# ── Validator edge cases ────────────────────────────────────────────────


def test_bbox_accepts_degenerate_but_ordered_box() -> None:
    assert BBox(x0=5.0, y0=5.0, x1=5.0, y1=5.0).x1 == 5.0


def test_source_span_accepts_zero_length_span() -> None:
    assert SourceSpan(start=4, end=4).end == 4


def test_page_range_accepts_single_page_range() -> None:
    assert PageRange(start=2, end=2).end == 2


def test_page_range_rejects_zero_end() -> None:
    with pytest.raises(ValidationError):
        PageRange(start=1, end=0)


def test_quality_signal_rejects_empty_name() -> None:
    with pytest.raises(ValidationError):
        QualitySignal(name="", score=0.5)


def test_quality_signal_rejects_negative_score() -> None:
    with pytest.raises(ValidationError):
        QualitySignal(name="density", score=-0.01)


def test_chunk_relation_rejects_empty_target() -> None:
    with pytest.raises(ValidationError):
        ChunkRelation(kind=RelationKind.CAPTION_OF, target_id="")


def test_chunk_rejects_zero_page_number() -> None:
    with pytest.raises(ValidationError):
        StructuredChunk(
            id="c1",
            kind=ChunkKind.PARAGRAPH,
            content="hello",
            page_number=0,
            reading_order=0,
        )


def test_chunk_rejects_negative_reading_order() -> None:
    with pytest.raises(ValidationError):
        StructuredChunk(
            id="c1",
            kind=ChunkKind.PARAGRAPH,
            content="hello",
            page_number=1,
            reading_order=-1,
        )


def test_page_rejects_negative_dimensions() -> None:
    with pytest.raises(ValidationError):
        PageResult(page_number=1, width=-1.0)
    with pytest.raises(ValidationError):
        PageResult(page_number=1, height=-1.0)


def test_metadata_rejects_bad_hash_and_counts() -> None:
    with pytest.raises(ValidationError, match="pattern"):
        DocumentMetadata(
            source_uri="file:///x.pdf",
            source_hash="not-a-hash",
            format="pdf",
            page_count=1,
            produced_at=datetime(2026, 1, 1, tzinfo=UTC),
            package_version="0.0.0",
        )
    with pytest.raises(ValidationError):
        DocumentMetadata(
            source_uri="file:///x.pdf",
            source_hash=_SHA,
            format="pdf",
            page_count=-1,
            produced_at=datetime(2026, 1, 1, tzinfo=UTC),
            package_version="0.0.0",
        )
    with pytest.raises(ValidationError):
        DocumentMetadata(
            source_uri="",
            source_hash=_SHA,
            format="pdf",
            page_count=1,
            produced_at=datetime(2026, 1, 1, tzinfo=UTC),
            package_version="0.0.0",
        )


def test_document_rejects_pages_when_page_count_is_zero() -> None:
    with pytest.raises(ValidationError, match="page_count"):
        DocumentResult(metadata=_metadata(page_count=0), pages=[PageResult(page_number=1)])


def test_document_rejects_duplicate_ids_between_parent_and_child() -> None:
    nested = _chunk("same", 1, 1)
    nested.children = [_chunk("same", 1, 1)]
    with pytest.raises(ValidationError, match="duplicate chunk ids: \\['same'\\]"):
        DocumentResult(
            metadata=_metadata(page_count=1),
            pages=[PageResult(page_number=1, blocks=[_chunk("other", 1, 0), nested])],
        )


def _chunk(chunk_id: str = "c1", page: int = 1, order: int = 0) -> StructuredChunk:
    return StructuredChunk(
        id=chunk_id,
        kind=ChunkKind.PARAGRAPH,
        content="hello",
        page_number=page,
        reading_order=order,
    )


def _metadata(page_count: int = 1) -> DocumentMetadata:
    return DocumentMetadata(
        source_uri="file:///sample.pdf",
        source_hash=_SHA,
        format="pdf",
        page_count=page_count,
        produced_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        package_version="0.0.0",
    )


# ── Trace / failure coupling: bijection across every status ────────────


@pytest.mark.parametrize("status", [PassStatus.OK, PassStatus.SKIPPED, PassStatus.CANCELLED])
def test_failure_record_is_rejected_for_non_failed_status(status: PassStatus) -> None:
    with pytest.raises(ValidationError, match="must appear together"):
        TraceEntry(
            pass_kind=PassKind.NATIVE,
            backend="docling",
            backend_version="2.0",
            status=status,
            elapsed_s=1.0,
            failure=_failure(),
        )


# ── Trace / failure coupling ─────────────────────────────────────────────


def _failure() -> PassFailure:
    return PassFailure(
        code=FailureCode.BUDGET_EXCEEDED,
        pass_kind=PassKind.NATIVE,
        backend="docling",
        backend_version="2.0",
        budget_s=30.0,
        elapsed_s=31.2,
        detail="page range exceeded budget",
        occurred_at=datetime(2026, 1, 2, tzinfo=UTC),
    )


def test_pass_failure_rejects_negative_elapsed_and_empty_backend() -> None:
    with pytest.raises(ValidationError):
        PassFailure(
            code=FailureCode.TIMEOUT,
            pass_kind=PassKind.NATIVE,
            backend="docling",
            backend_version="2.0",
            budget_s=1.0,
            elapsed_s=-0.5,
            detail="x",
            occurred_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
    with pytest.raises(ValidationError):
        PassFailure(
            code=FailureCode.TIMEOUT,
            pass_kind=PassKind.NATIVE,
            backend="",
            backend_version="2.0",
            budget_s=1.0,
            elapsed_s=1.0,
            detail="x",
            occurred_at=datetime(2026, 1, 2, tzinfo=UTC),
        )


def test_detected_region_and_asset_validate_fields() -> None:
    with pytest.raises(ValidationError):
        DetectedRegion(id="r1", kind=RegionKind.TEXT, bbox=BBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0), confidence=1.5)
    with pytest.raises(ValidationError, match="pattern"):
        ExtractedAsset(id="a1", kind=AssetKind.IMAGE, media_type="image/png", uri="file:///a.png", sha256="xyz")
    with pytest.raises(ValidationError):
        ExtractedAsset(id="a1", kind=AssetKind.IMAGE, media_type="image/png", uri="")
