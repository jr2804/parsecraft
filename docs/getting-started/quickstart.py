"""Quickstart: build a DocumentResult and project it to Markdown."""

from __future__ import annotations

from parsecraft.ir import (
    ChunkKind,
    DocumentMetadata,
    DocumentResult,
    PageResult,
    StructuredChunk,
    utcnow,
)
from parsecraft.ir.markdown import to_markdown

result = DocumentResult(
    metadata=DocumentMetadata(
        source_uri="memory://demo",
        source_hash="0" * 64,
        format="text/plain",
        page_count=1,
        produced_at=utcnow(),
        package_version="0.0.0",
    ),
    pages=[
        PageResult(
            page_number=1,
            blocks=[
                StructuredChunk(
                    id="p1-0",
                    kind=ChunkKind.HEADING,
                    content="Hello",
                    page_number=1,
                    reading_order=0,
                    metadata={"heading_level": "1"},
                ),
                StructuredChunk(
                    id="p1-1",
                    kind=ChunkKind.PARAGRAPH,
                    content="Typed chunks project to Markdown.",
                    page_number=1,
                    reading_order=1,
                ),
            ],
        ),
    ],
)

print(to_markdown(result))
