"""Light factory for the LiteParse backend (heavy work in ``_impl``).

Discovery imports this module, never ``liteparse`` itself; the heavy impl
loads through :func:`importlib.import_module` at instantiation.

``supported_formats`` lists only inputs verified against liteparse 2.14.7 on
2026-09-27: PDF and PNG/JPEG/TIFF parse; Office/ODF need a system LibreOffice
(unverified here, deliberately undeclared) and ``.html`` is rejected by
liteparse itself (``unsupported file format``).
"""

from __future__ import annotations

import importlib
from typing import Protocol, runtime_checkable

from parsecraft.backends.errors import BackendError
from parsecraft.backends.protocol import (
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    DocumentBackend,
)

#: Backend version (adapter code, not the upstream parser).
LITEPARSE_BACKEND_VERSION = "0.1.0"

#: Verified MIME inputs (liteparse 2.14.7): PDF + PNG/JPEG/TIFF images.
LITEPARSE_FORMATS: tuple[str, ...] = (
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/tiff",
)

_EXTRA = "liteparse"
_IMPL_MODULE = "parsecraft.backends.liteparse._impl"

CAPABILITIES = BackendCapabilities(
    supported_formats=list(LITEPARSE_FORMATS),
    supports_page_ranges=True,
    supports_multi_page=True,
    requires_gpu=False,
    estimated_vram_gb=None,
    optional_dependency_group=_EXTRA,
    model_asset=None,  # local library — no model weights to pin
)

DESCRIPTOR = BackendDescriptor(
    name="liteparse",
    version=LITEPARSE_BACKEND_VERSION,
    capabilities=CAPABILITIES,
)


@runtime_checkable
class _ImplModule(Protocol):
    """Shape the light factory needs from the heavy impl module (OCR-style seam)."""

    def create(self, config: BackendConfig) -> DocumentBackend:
        """Instantiate the backend — the sanctioned heavy-import boundary."""
        ...


class LiteparseFactory:
    """Light factory: resolves the heavy impl at instantiation, never before."""

    descriptor: BackendDescriptor = DESCRIPTOR

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        try:
            module = importlib.import_module(_IMPL_MODULE)
        except ImportError as exc:
            msg = f"backend 'liteparse' requires the 'liteparse' extra — pip install 'parsecraft[liteparse]' (missing module: {exc.name})"
            raise BackendError(msg) from exc
        if not isinstance(module, _ImplModule):
            msg = f"impl module {_IMPL_MODULE!r} must expose create(config)"
            raise BackendError(msg)
        return module.create(config)


factory = LiteparseFactory()
