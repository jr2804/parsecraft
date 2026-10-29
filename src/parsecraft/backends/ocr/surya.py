"""Light entry point for the SuryaOCR 2.5 backend (heavy work in ``_surya_impl``).

Discovery imports this module; the Transformers stack loads only when the
registry instantiates the backend. Entry point: ``ocr-surya`` →
``parsecraft.backends.ocr.surya:factory``. Surya 2.5 is the OCR checkpoint
behind marker's accuracy — wired here directly, so ``marker-pdf`` stays out of
the graph (pc-9gz, ADR-0006 decision 2).
"""

from __future__ import annotations

from parsecraft.backends.ocr._common import load_impl
from parsecraft.backends.ocr._models import (
    OCR_BACKEND_VERSION,
    SURYA,
    SURYA_CAPABILITIES,
)
from parsecraft.backends.protocol import BackendConfig, BackendDescriptor, DocumentBackend

_IMPL_MODULE = "parsecraft.backends.ocr._surya_impl"

DESCRIPTOR = BackendDescriptor(
    version=OCR_BACKEND_VERSION,
    name=SURYA.name,
    capabilities=SURYA_CAPABILITIES,
)


class SuryaFactory:
    """Light factory: resolves the heavy impl at instantiation, never before."""

    descriptor: BackendDescriptor = DESCRIPTOR

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        impl = load_impl(_IMPL_MODULE, extra=SURYA.extra)
        return impl.create(config)


factory = SuryaFactory()
