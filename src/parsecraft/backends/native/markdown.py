"""Markdown native backend — delegates to the Markdown input adapter.

Parsing lives in :mod:`parsecraft.adapters.markdown` (the single IR input
path); this backend only wraps it in the backend contract.
"""

from __future__ import annotations

from parsecraft.adapters.markdown import parse_markdown
from parsecraft.backends.native._common import NATIVE_BACKEND_VERSION, NativeBackendBase
from parsecraft.backends.protocol import (
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    BackendFactory,
    DocumentBackend,
    SourceDocument,
)
from parsecraft.ir.models import PageResult

BACKEND_NAME = "native-markdown"
SUPPORTED_FORMATS = ["text/markdown"]


class MarkdownBackend(NativeBackendBase):
    """Markdown document converted through ``parse_markdown``."""

    name = BACKEND_NAME


class _MarkdownFactory:
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
        return MarkdownBackend(config, self.descriptor.capabilities, extract=extract_markdown_pages)


def extract_markdown_pages(source: SourceDocument, data: bytes) -> list[PageResult]:
    """Parse Markdown bytes via the input adapter and return its IR pages."""
    document = parse_markdown(data.decode("utf-8", errors="replace"), source.uri)
    return document.pages


factory: BackendFactory = _MarkdownFactory()
