"""Light entry point for the TeleOCR OCR backend (heavy work in ``_tele_impl``).

Discovery imports this module; the Transformers stack loads only when the
registry instantiates the backend. Entry point: ``ocr-tele`` →
``parsecraft.backends.ocr.tele:factory``.
"""

from __future__ import annotations

from parsecraft.backends.ocr._common import load_impl
from parsecraft.backends.ocr._models import (
    OCR_BACKEND_VERSION,
    TELE,
    TELE_CAPABILITIES,
)
from parsecraft.backends.protocol import BackendConfig, BackendDescriptor, DocumentBackend

_IMPL_MODULE = "parsecraft.backends.ocr._tele_impl"

DESCRIPTOR = BackendDescriptor(
    version=OCR_BACKEND_VERSION,
    name=TELE.name,
    capabilities=TELE_CAPABILITIES,
)


class TeleFactory:
    """Light factory: resolves the heavy impl at instantiation, never before."""

    descriptor: BackendDescriptor = DESCRIPTOR

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        impl = load_impl(_IMPL_MODULE, extra=TELE.extra)
        return impl.create(config)


factory = TeleFactory()
