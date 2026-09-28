"""Light factory for the Docling backend (heavy work in ``_impl``).

Discovery imports this module, never ``docling`` itself; the heavy impl loads
through :func:`importlib.import_module` at instantiation.

``supported_formats`` lists only inputs conversion-verified against docling
2.130.0 on 2026-09-28: PDF, HTML, Markdown, and plain text. The library also
registers docx/pptx/xlsx/odt/ods/odp/rtf/xls and image formats, but those were
not conversion-verified here and stay deliberately undeclared until they are —
the same contract LiteParse follows.
"""

from __future__ import annotations

import importlib
from typing import Protocol, runtime_checkable

from parsecraft.backends.errors import BackendError, DependencyUnavailableError
from parsecraft.backends.protocol import (
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    DocumentBackend,
)

#: Backend version (adapter code, not the upstream library).
DOCLING_BACKEND_VERSION = "0.1.0"

#: Conversion-verified MIME inputs (docling 2.130.0, 2026-09-28).
DOCLING_FORMATS: tuple[str, ...] = (
    "application/pdf",
    "text/html",
    "text/markdown",
    "text/plain",
)

_EXTRA = "docling"
_IMPL_MODULE = "parsecraft.backends.docling._impl"

CAPABILITIES = BackendCapabilities(
    supported_formats=list(DOCLING_FORMATS),
    supports_page_ranges=True,
    supports_multi_page=True,
    requires_gpu=False,
    estimated_vram_gb=None,
    optional_dependency_group=_EXTRA,
    model_asset=None,  # local library; layout models ship with docling
)

DESCRIPTOR = BackendDescriptor(
    name="docling",
    version=DOCLING_BACKEND_VERSION,
    capabilities=CAPABILITIES,
)


@runtime_checkable
class _ImplModule(Protocol):
    """Shape the light factory needs from the heavy impl module (OCR-style seam)."""

    def create(self, config: BackendConfig) -> DocumentBackend:
        """Instantiate the backend — the sanctioned heavy-import boundary."""
        ...


class DoclingFactory:
    """Light factory: resolves the heavy impl at instantiation, never before."""

    descriptor: BackendDescriptor = DESCRIPTOR

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        try:
            module = importlib.import_module(_IMPL_MODULE)
        except ImportError as exc:
            raise DependencyUnavailableError(exc.name or "docling", _EXTRA) from exc
        if not isinstance(module, _ImplModule):
            msg = f"impl module {_IMPL_MODULE!r} must expose create(config)"
            raise BackendError(msg)
        return module.create(config)


factory = DoclingFactory()
