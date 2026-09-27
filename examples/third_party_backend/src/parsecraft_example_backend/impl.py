"""Echo backend implementation — imported only when the factory is called.

Mirrors how a real OCR backend must be written: model/runtime imports stay in
this module (or deeper), never in the entry-point module.
"""

from __future__ import annotations

import hashlib

from parsecraft.backends import (
    AnalysisResult,
    BackendCapabilities,
    BackendConfig,
    BackendRef,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
    PageSignal,
    SourceDocument,
)
from parsecraft.ir.models import ChunkKind, PageResult, StructuredChunk

_BACKEND_NAME = "example-echo"
_BACKEND_VERSION = "0.1.0"


class EchoBackend:
    """Deterministic toy backend: one paragraph per requested page."""

    name = _BACKEND_NAME
    capabilities: BackendCapabilities

    def __init__(self, config: BackendConfig, capabilities: BackendCapabilities) -> None:
        self._config = config
        self.capabilities = capabilities

    def analyze(self, source: SourceDocument) -> AnalysisResult:
        payload = source.content if source.content is not None else source.uri.encode()
        source_hash = hashlib.sha256(payload).hexdigest()
        return AnalysisResult(
            source_hash=source_hash,
            page_count=0,
            signals=[
                PageSignal(
                    page_number=1,
                    has_native_text=True,
                    text_chars=len(payload),
                    image_count=0,
                    blank=not payload,
                )
            ],
        )

    def convert(self, request: ConversionRequest) -> BackendResult:
        page_range = request.page_range
        first = page_range.start if page_range is not None else 1
        last = page_range.end if page_range is not None else 1
        pages = [
            PageResult(
                page_number=number,
                blocks=[
                    StructuredChunk(
                        id=f"echo-{number}-b0",
                        kind=ChunkKind.PARAGRAPH,
                        content=f"echo page {number}",
                        page_number=number,
                        reading_order=0,
                    )
                ],
            )
            for number in range(first, last + 1)
        ]
        return BackendResult(
            backend=BackendRef(name=self.name, version=_BACKEND_VERSION),
            pages=pages,
            elapsed_s=0.0,
        )
