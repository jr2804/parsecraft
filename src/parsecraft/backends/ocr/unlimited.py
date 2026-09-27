"""Light entry point for the Unlimited-OCR backend (heavy work in ``_unlimited_impl``).

Discovery imports this module; the Transformers stack loads only when the
registry instantiates the backend. Entry point: ``ocr-unlimited`` →
``parsecraft.backends.ocr.unlimited:factory``.
"""

from __future__ import annotations

from parsecraft.backends.ocr._common import load_impl
from parsecraft.backends.ocr._models import (
    OCR_BACKEND_VERSION,
    UNLIMITED_CAPABILITIES,
    UNLIMITED_EXTRA,
    UNLIMITED_NAME,
)
from parsecraft.backends.protocol import BackendConfig, BackendDescriptor, DocumentBackend

_IMPL_MODULE = "parsecraft.backends.ocr._unlimited_impl"

DESCRIPTOR = BackendDescriptor(
    version=OCR_BACKEND_VERSION,
    name=UNLIMITED_NAME,
    capabilities=UNLIMITED_CAPABILITIES,
)


class UnlimitedFactory:
    """Light factory: resolves the heavy impl at instantiation, never before."""

    descriptor: BackendDescriptor = DESCRIPTOR

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        impl = load_impl(_IMPL_MODULE, backend=UNLIMITED_NAME, extra=UNLIMITED_EXTRA)
        return impl.create(config)


factory = UnlimitedFactory()
