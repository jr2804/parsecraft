"""Type-level consumer of the public API — checked by ``ty``, never executed.

If the package's exported types regress (``Any`` leaks, missing annotations,
narrowing breaks), ``ty check src/ tests/`` fails on this file even when every
runtime test passes.
"""

from __future__ import annotations

from datetime import UTC, datetime

from parsecraft.backends import (
    BackendDescriptor,
    BackendRegistry,
    ConversionRequest,
    SourceDocument,
)
from parsecraft.ir import (
    ChunkKind,
    DocumentMetadata,
    DocumentResult,
    PageResult,
    StructuredChunk,
    to_markdown,
)


def build_document() -> DocumentResult:
    """Construct a document through the fully typed IR surface."""
    chunk = StructuredChunk(
        id="c-1",
        kind=ChunkKind.HEADING,
        content="Typed",
        page_number=1,
        reading_order=0,
        metadata={"heading_level": "2"},
    )
    metadata = DocumentMetadata(
        source_uri="file:///consumer.pdf",
        source_hash="c" * 64,
        format="pdf",
        page_count=1,
        produced_at=datetime.now(UTC),
        package_version="0.0.0",
    )
    return DocumentResult(metadata=metadata, pages=[PageResult(page_number=1, blocks=[chunk])])


def consume_registry(registry: BackendRegistry) -> list[str]:
    """Read descriptors and build a request with concrete types only."""
    descriptors: list[BackendDescriptor] = registry.list_backends()
    request = ConversionRequest(
        source=SourceDocument(uri="file:///consumer.pdf"),
        max_output_chars=1_000_000,
    )
    _ = request.max_output_chars
    return [descriptor.name for descriptor in descriptors]


def project(document: DocumentResult) -> str:
    """Round-trip IR through the deterministic projection."""
    return to_markdown(document)
