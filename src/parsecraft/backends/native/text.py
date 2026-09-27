"""Plain-text native backend — dependency-free."""

from __future__ import annotations

from parsecraft.backends.native._common import NATIVE_BACKEND_VERSION, NativeBackendBase, paragraph_chunks
from parsecraft.backends.protocol import (
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    BackendFactory,
    DocumentBackend,
    SourceDocument,
)
from parsecraft.ir.models import PageResult

BACKEND_NAME = "native-text"
SUPPORTED_FORMATS = ["text/plain"]


class TextBackend(NativeBackendBase):
    """Plain text: one PARAGRAPH chunk per blank-line-separated block."""

    name = BACKEND_NAME


class _TextFactory:
    """Light entry-point object: descriptor + build."""

    descriptor = BackendDescriptor(
        name=BACKEND_NAME,
        version=NATIVE_BACKEND_VERSION,
        capabilities=BackendCapabilities(
            supported_formats=SUPPORTED_FORMATS,
            supports_page_ranges=False,
            supports_multi_page=False,
        ),
    )

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        return TextBackend(config, self.descriptor.capabilities, extract=extract_text_pages)


def extract_text_pages(source: SourceDocument, data: bytes) -> list[PageResult]:
    """One page of paragraph chunks split on blank lines."""
    text = data.decode("utf-8", errors="replace")
    return [PageResult(page_number=1, blocks=paragraph_chunks("native-text", 1, text))]


factory: BackendFactory = _TextFactory()
