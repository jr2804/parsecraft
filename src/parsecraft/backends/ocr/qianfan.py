"""Light entry point for the Qianfan-OCR backend (heavy work in ``_qianfan_impl``).

Discovery imports this module; the Transformers stack loads only when the
registry instantiates the backend. Entry point: ``ocr-qianfan`` →
``parsecraft.backends.ocr.qianfan:factory``.
"""

from __future__ import annotations

from parsecraft.backends.ocr._common import load_impl
from parsecraft.backends.ocr._models import (
    OCR_BACKEND_VERSION,
    QIANFAN_CAPABILITIES,
    QIANFAN_EXTRA,
    QIANFAN_NAME,
)
from parsecraft.backends.protocol import BackendConfig, BackendDescriptor, DocumentBackend

_IMPL_MODULE = "parsecraft.backends.ocr._qianfan_impl"

DESCRIPTOR = BackendDescriptor(
    version=OCR_BACKEND_VERSION,
    name=QIANFAN_NAME,
    capabilities=QIANFAN_CAPABILITIES,
)


class QianfanFactory:
    """Light factory: resolves the heavy impl at instantiation, never before."""

    descriptor: BackendDescriptor = DESCRIPTOR

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        impl = load_impl(_IMPL_MODULE, backend=QIANFAN_NAME, extra=QIANFAN_EXTRA)
        return impl.create(config)


factory = QianfanFactory()
