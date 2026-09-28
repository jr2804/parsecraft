"""Light entry point for the OvisOCR2 OCR backend (heavy work in ``_ovis_impl``).

Discovery imports this module; the Transformers stack loads only when the
registry instantiates the backend. Entry point: ``ocr-ovis`` →
``parsecraft.backends.ocr.ovis:factory``.
"""

from __future__ import annotations

from parsecraft.backends.ocr._common import load_impl
from parsecraft.backends.ocr._models import (
    OCR_BACKEND_VERSION,
    OVIS_CAPABILITIES,
    OVIS_EXTRA,
    OVIS_NAME,
)
from parsecraft.backends.protocol import BackendConfig, BackendDescriptor, DocumentBackend

_IMPL_MODULE = "parsecraft.backends.ocr._ovis_impl"

DESCRIPTOR = BackendDescriptor(
    version=OCR_BACKEND_VERSION,
    name=OVIS_NAME,
    capabilities=OVIS_CAPABILITIES,
)


class OvisFactory:
    """Light factory: resolves the heavy impl at instantiation, never before."""

    descriptor: BackendDescriptor = DESCRIPTOR

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        impl = load_impl(_IMPL_MODULE, extra=OVIS_EXTRA)
        return impl.create(config)


factory = OvisFactory()
