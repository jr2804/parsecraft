"""Light factory for the Marker backend (heavy work in ``_impl``).

Discovery imports this module, never ``marker`` itself; the heavy impl loads
through :func:`importlib.import_module` at instantiation.

``supported_formats`` is ``["application/pdf"]`` only — per ADR-0006, the
upstream tool advertises DOCX/XLSX/PPTX/HTML/EPUB, but those route through
weasyprint which needs GTK/Pango system libraries and fails at import on
Windows (the canonical platform). Other formats are declared upstream but
undocumented here until conversion-verified (pdf-inspector precedent, gh-2).

Marker is a **converter candidate, not an analyzer** (ADR-0006): it has no
cheap detection API, so ``analyze()`` provides a minimal signal and the
planner's native-first ranking is untouched.
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

#: Backend version (adapter code, not the upstream marker release).
MARKER_BACKEND_VERSION = "0.1.0"

#: Verified MIME inputs (marker-pdf 2.0.0, 2026-10-08): PDF only — see
#: module docstring and ADR-0006.
MARKER_FORMATS: tuple[str, ...] = ("application/pdf",)

_EXTRA = "marker"
_IMPL_MODULE = "parsecraft.backends.marker._impl"

CAPABILITIES = BackendCapabilities(
    supported_formats=list(MARKER_FORMATS),
    supports_page_ranges=True,
    supports_multi_page=True,
    estimated_vram_gb=None,
    optional_dependency_group=_EXTRA,
    model_asset=None,  # models download at converter creation (ADR-0006)
)

DESCRIPTOR = BackendDescriptor(
    name="marker",
    version=MARKER_BACKEND_VERSION,
    capabilities=CAPABILITIES,
)


@runtime_checkable
class _ImplModule(Protocol):
    """Shape the light factory needs from the heavy impl module."""

    def create(self, config: BackendConfig) -> DocumentBackend:
        """Instantiate the backend — the sanctioned heavy-import boundary."""
        ...


class MarkerFactory:
    """Light factory: resolves the heavy impl at instantiation, never before."""

    descriptor: BackendDescriptor = DESCRIPTOR

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        try:
            module = importlib.import_module(_IMPL_MODULE)
        except ImportError as exc:
            raise DependencyUnavailableError(exc.name or "marker", _EXTRA) from exc
        if not isinstance(module, _ImplModule):
            msg = f"impl module {_IMPL_MODULE!r} must expose create(config)"
            raise BackendError(msg)
        return module.create(config)


factory = MarkerFactory()
