"""Light entry point for the native MinerU2.5 backend.

Heavy work lives in ``parsecraft.backends.mineru._impl`` and is pulled in at
instantiation via ``importlib`` — never an inline import (pyreorder hoists
those). Entry point: ``parsecraft.backends.mineru.mineru:factory``.

The conversion runs the Apache-2.0 ``MinerU2.5-Pro-2605-1.2B`` checkpoint
directly through stock transformers — the ``mineru`` pip package is NOT a
dependency. The two-step protocol (layout detection, then per-block content
recognition) is ported from ``mineru-vl-utils`` (Apache-2.0, MinerU Team; see
ADR-0007 for the attribution record). Licence surface (ADR-0007, amended
2026-10-26): everything this backend touches declares Apache-2.0 — the
MinerU-OSL code licence and the unlicensed ONNX/GGUF kits of the packaged
engine drop out with the package.
"""

from __future__ import annotations

import importlib
from typing import Protocol, runtime_checkable

from parsecraft.backends.errors import BackendError, DependencyUnavailableError
from parsecraft.backends.protocol import (
    GPU_OPTIONAL,
    AssetFilePin,
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    DocumentBackend,
    ModelAssetDescriptor,
)

MINERU_NAME = "mineru"
MINERU_BACKEND_VERSION = "0.2.0"

# Conversion-verified MIME inputs: PDF only (verified against the packaged
# engine 2026-10-09; re-verified against the native protocol 2026-10-26).
# Office/image/HTML formats stay undeclared until conversion-verified (house
# rule from gh-2: a format enters ``supported_formats`` only after
# verification).
MINERU_FORMATS: tuple[str, ...] = ("application/pdf",)

_EXTRA = "mineru"
_IMPL_MODULE = "parsecraft.backends.mineru._impl"

# ---------------------------------------------------------------------------
# Model asset — parsecraft owns the download (ADR-0007, amended 2026-10-26).
#
# The checkpoint is a stock ``Qwen2VLForConditionalGeneration`` (no custom
# modeling code), every pinned file declares Apache-2.0, and each file's
# sha256/size was taken from the Hub API at the pinned revision. The protocol
# constants the impl ports (prompts, layout grammar, normalization rules) come
# from ``mineru-vl-utils`` (Apache-2.0) — attribution lives in ADR-0007.
# ---------------------------------------------------------------------------
MINERU_VLM_ID = "opendatalab/MinerU2.5-Pro-2605-1.2B"
MINERU_VLM_REVISION = "08aaea840498d49ce16247b6263196cf02814885"
MINERU_ASSET = ModelAssetDescriptor(
    model_id=MINERU_VLM_ID,
    model_revision=MINERU_VLM_REVISION,
    model_license="apache-2.0",
    code_license="apache-2.0",
    asset_license="apache-2.0",
    requires_user_acceptance=False,
    source_urls=[
        f"https://huggingface.co/{MINERU_VLM_ID}",
        "https://github.com/opendatalab/mineru-vl-utils",
        "https://github.com/opendatalab/MinerU",
    ],
    # Sum of the pinned files (weights 2.31 GB + tokenizer/processor files).
    size_bytes=2_328_011_610,
    quantization=None,
    # 1.2B BF16 ≈ 2.3 GiB of weights + vision/text activations at the layout
    # resolution (1036×1036): 4 GiB is the measured-order footprint the
    # planner may budget against an 8 GiB card.
    estimated_vram_gb=4.0,
    file_pins=(
        AssetFilePin(
            path="added_tokens.json",
            sha256="14155f41a9e51bee8ee7e5ff1b458418928d9ea86822ea5e98ae7bbdf1912ec8",
            size=800,
        ),
        AssetFilePin(
            path="chat_template.jinja",
            sha256="a0bc6f6fc7a29a80017a433e8f03a1cc1236e838a944a2d034295a60c4f2fddb",
            size=1017,
        ),
        AssetFilePin(
            path="config.json",
            sha256="f91cad3c52a3d8f1d420b51459455b27d792db28bf5d1d08097ae96bf1c44730",
            size=2871,
        ),
        AssetFilePin(
            path="generation_config.json",
            sha256="405a603af8aed51b82ef71a5e16e7c053d11576fdc2db3c168d32bac2fd75f0d",
            size=215,
        ),
        AssetFilePin(
            path="merges.txt",
            sha256="8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5",
            size=1_671_853,
        ),
        AssetFilePin(
            path="preprocessor_config.json",
            sha256="7070ae84a684ce2eb8d239c2cb38ff848085075784b213ea28a5ef5b3cdb445f",
            size=316,
        ),
        AssetFilePin(
            path="special_tokens_map.json",
            sha256="badbadb9452d98337219d280879a65eafe224d661a0216114c7cbd3e40ff4984",
            size=919,
        ),
        AssetFilePin(
            path="tokenizer_config.json",
            sha256="d762430b9c668b5c3ad95e26626e3982d5b2a59c18ff214d621d1de4318ff376",
            size=6596,
        ),
        AssetFilePin(
            path="vocab.json",
            sha256="ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910",
            size=2_776_833,
        ),
        AssetFilePin(
            path="model.safetensors",
            sha256="abf8681ca63b8dec7b67de257af47b821f179442f72998d0696ae2ed9232a5f0",
            size=2_312_126_640,
        ),
        AssetFilePin(
            path="tokenizer.json",
            sha256="dceac5fc54a795ee7570d17902b47bd05412dc2afa62bdf325c3f97fcb5b87fe",
            size=11_423_550,
        ),
    ),
)

CAPABILITIES = BackendCapabilities(
    supported_formats=list(MINERU_FORMATS),
    # Honoured natively: the per-page loop rasterizes exactly the requested
    # window (an improvement over the packaged engine's post-filter).
    supports_page_ranges=True,
    supports_multi_page=True,
    # The default effort ("flash") is a weight-free text-layer path; the VLM
    # path (higher effort, scanned pages) is a speedup, never a precondition.
    gpu_requirement=GPU_OPTIONAL,
    # The 1.2B checkpoint's footprint (see MINERU_ASSET) — declared now that
    # parsecraft owns the weights and the size is a fact, not a guess.
    estimated_vram_gb=4.0,
    optional_dependency_group=_EXTRA,
    convert_extra=_EXTRA,
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
