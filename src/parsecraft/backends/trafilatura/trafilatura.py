"""Light factory for the trafilatura backend (heavy work in ``_impl``).

Discovery imports this module, never ``trafilatura`` itself; the heavy impl
loads through :func:`importlib.import_module` at instantiation.
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
TRAFILATURA_BACKEND_VERSION = "0.1.0"

#: Conversion-verified MIME inputs (trafilatura 2.3.1, 2026-10-09).
TRAFILATURA_FORMATS: tuple[str, ...] = ("text/html",)

_EXTRA = "trafilatura"
_IMPL_MODULE = "parsecraft.backends.trafilatura._impl"

CAPABILITIES = BackendCapabilities(
    supported_formats=list(TRAFILATURA_FORMATS),
    supports_page_ranges=False,
    supports_multi_page=False,
    estimated_vram_gb=None,
    optional_dependency_group=_EXTRA,
    model_asset=None,  # asset-free: no weights to fetch (ADR-0006 does not apply)
)

DESCRIPTOR = BackendDescriptor(
    name="trafilatura",
    version=TRAFILATURA_BACKEND_VERSION,
    capabilities=CAPABILITIES,
)


@runtime_checkable
class _ImplModule(Protocol):
    """Shape the light factory needs from the heavy impl module."""

    def create(self, config: BackendConfig) -> DocumentBackend:
        """Instantiate the backend — the sanctioned heavy-import boundary."""
        ...


class TrafilaturaFactory:
    """Light factory: resolves the heavy impl at instantiation, never before."""

    descriptor: BackendDescriptor = DESCRIPTOR

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        try:
            module = importlib.import_module(_IMPL_MODULE)
        except ImportError as exc:
            raise DependencyUnavailableError(exc.name or "trafilatura", _EXTRA) from exc
        if not isinstance(module, _ImplModule):
            msg = f"impl module {_IMPL_MODULE!r} must expose create(config)"
            raise BackendError(msg)
        return module.create(config)


factory = TrafilaturaFactory()
