"""Light entry point for the MinerU backend.

Heavy work lives in ``parsecraft.backends.mineru._impl`` and is pulled in at
instantiation via ``importlib`` — never an inline import (pyreorder hoists
those). Entry point: ``parsecraft.backends.mineru.mineru:factory``.

Licence surface (ADR-0007, amended 2026-10-09): MinerU's weights declare
Apache-2.0 for the VLM checkpoint and the torch kit; the Windows/CPU default
llama-cpp GGUF engine and the ONNX kit declare **no licence**; the code
carries ``LicenseRef-MinerU-Open-Source-License``. See ``MINERU_ASSET`` and the
docs statement — never install this extra silently.
"""

from __future__ import annotations

import importlib
from typing import Protocol, runtime_checkable

from parsecraft.backends.errors import BackendError, DependencyUnavailableError
from parsecraft.backends.protocol import (
    GPU_OPTIONAL,
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    DocumentBackend,
    ModelAssetDescriptor,
)

MINERU_NAME = "mineru"
MINERU_BACKEND_VERSION = "0.1.0"

# Conversion-verified MIME inputs (mineru 4.0.11, verified 2026-10-09): PDF
# only — an 80-page text PDF converted at effort="flash" in 21.6 s (1382
# content-list items, ``page_idx`` 0-based). Office/image/HTML formats exist
# upstream but are NOT conversion-verified here and stay undeclared (house
# rule from gh-2: a format enters ``supported_formats`` only after
# verification).
MINERU_FORMATS: tuple[str, ...] = ("application/pdf",)

_EXTRA = "mineru"
_IMPL_MODULE = "parsecraft.backends.mineru._impl"

# ---------------------------------------------------------------------------
# Model asset — the licence surface (ADR-0007, amended 2026-10-09).
#
# MinerU downloads its OWN weights at conversion time into
# ``$MINERU_HOME/models``; parsecraft does not fetch or verify them, so
# ``file_pins`` is deliberately EMPTY — unlike the OCR family, whose downloads
# parsecraft owns and pins.
# ---------------------------------------------------------------------------
MINERU_VLM_ID = "opendatalab/MinerU2.5-Pro-2605-1.2B"
MINERU_VLM_REVISION = "08aaea840498d49ce16247b6263196cf02814885"
MINERU_ASSET = ModelAssetDescriptor(
    model_id=MINERU_VLM_ID,
    model_revision=MINERU_VLM_REVISION,
    model_license="apache-2.0",
    code_license="LicenseRef-MinerU-Open-Source-License",
    asset_license="apache-2.0 (torch kit); undeclared (onnx kit, Windows-default GGUF engine)",
    requires_user_acceptance=False,
    source_urls=[
        f"https://huggingface.co/{MINERU_VLM_ID}",
        "https://huggingface.co/opendatalab/MinerU-4_models_torch",
        "https://huggingface.co/jinzhenj/MinerU2.5-Pro-2605-1.2B-GGUF",
        "https://github.com/opendatalab/MinerU",
    ],
    size_bytes=None,
    quantization=None,
    # MEASURED (2026-10-09): effort="flash" on a text PDF loads no weights at
    # all (MINERU_HOME/models stayed empty) -> 0 GiB. Scanned pages and effort
    # above "flash" load the VLM checkpoint and need a GPU; the executor's
    # timeout deadline bounds that case.
    estimated_vram_gb=0.0,
    file_pins=(),
)

CAPABILITIES = BackendCapabilities(
    supported_formats=list(MINERU_FORMATS),
    supports_page_ranges=True,  # honoured by post-filtering, see _impl
    supports_multi_page=True,
    # The verified default (text PDF at effort="flash") is weight-free CPU work;
    # the VLM path (scanned pages, higher effort) is a GPU speedup, never a
    # precondition for the declared surface.
    gpu_requirement=GPU_OPTIONAL,
    estimated_vram_gb=0.0,
    optional_dependency_group=_EXTRA,
    model_asset=MINERU_ASSET,
)

DESCRIPTOR = BackendDescriptor(
    name=MINERU_NAME,
    version=MINERU_BACKEND_VERSION,
    capabilities=CAPABILITIES,
)


@runtime_checkable
class _ImplModule(Protocol):
    """Shape the light factory needs from the heavy impl module."""

    def create(self, config: BackendConfig) -> DocumentBackend:
        """Instantiate the backend — the sanctioned heavy-import boundary."""
        ...


class MineruBackendFactory:
    """Light factory: resolves the heavy impl at instantiation, never before."""

    descriptor: BackendDescriptor = DESCRIPTOR

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        return _load_impl().create(config)


def _load_impl() -> _ImplModule:
    """Import the heavy impl module at instantiation time — the only heavy boundary."""
    try:
        module = importlib.import_module(_IMPL_MODULE)
    except ImportError as exc:
        raise DependencyUnavailableError(exc.name or _EXTRA, _EXTRA) from exc
    if not isinstance(module, _ImplModule):
        msg = f"impl module {_IMPL_MODULE!r} must expose create(config)"
        raise BackendError(msg)
    return module


factory = MineruBackendFactory()

__all__ = [
    "CAPABILITIES",
    "DESCRIPTOR",
    "MINERU_ASSET",
    "MINERU_BACKEND_VERSION",
    "MINERU_FORMATS",
    "MINERU_NAME",
    "MineruBackendFactory",
    "factory",
]
