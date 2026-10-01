"""Light factory for the pdf-inspector backend (heavy work in ``_impl``).

Discovery imports this module, never ``pdf_inspector`` itself; the heavy impl
loads through :func:`importlib.import_module` at instantiation.

``supported_formats`` lists only inputs conversion-verified against
pdf-inspector 1.25.2 on 2026-10-01: PDF, and nothing else — the library is a
PDF-only tool (no DOCX/PPTX/XLSX path exists upstream).

The wheel is a prebuilt Rust extension (``cp38-abi3``: Linux x86_64/aarch64,
macOS Intel/ARM, Windows x64) with no Python dependencies, so installing the
extra needs no Rust toolchain. It embeds no OCR models, PDFium, or ONNX
Runtime, and this backend never calls pdf-inspector's OCR entry points — the
OCR runtime those would need stays out of the loop.
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
PDF_INSPECTOR_BACKEND_VERSION = "0.1.0"

#: Conversion-verified MIME inputs (pdf-inspector 1.25.2, 2026-10-01).
PDF_INSPECTOR_FORMATS: tuple[str, ...] = ("application/pdf",)

_EXTRA = "pdf-inspector"
_IMPL_MODULE = "parsecraft.backends.pdf_inspector._impl"

CAPABILITIES = BackendCapabilities(
    supported_formats=list(PDF_INSPECTOR_FORMATS),
    supports_page_ranges=True,
    supports_multi_page=True,
    requires_gpu=False,
    estimated_vram_gb=None,
    optional_dependency_group=_EXTRA,
    model_asset=None,  # local Rust library; no weights downloaded
)

DESCRIPTOR = BackendDescriptor(
    name="pdf-inspector",
    version=PDF_INSPECTOR_BACKEND_VERSION,
    capabilities=CAPABILITIES,
)


@runtime_checkable
class _ImplModule(Protocol):
    """Shape the light factory needs from the heavy impl module (OCR-style seam)."""

    def create(self, config: BackendConfig) -> DocumentBackend:
        """Instantiate the backend — the sanctioned heavy-import boundary."""
        ...


class PdfInspectorFactory:
    """Light factory: resolves the heavy impl at instantiation, never before."""

    descriptor: BackendDescriptor = DESCRIPTOR

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        try:
            module = importlib.import_module(_IMPL_MODULE)
        except ImportError as exc:
            raise DependencyUnavailableError(exc.name or "pdf_inspector", _EXTRA) from exc
        if not isinstance(module, _ImplModule):
            msg = f"impl module {_IMPL_MODULE!r} must expose create(config)"
            raise BackendError(msg)
        return module.create(config)


factory = PdfInspectorFactory()
