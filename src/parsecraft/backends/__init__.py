"""Backend protocol + registry — the extension surface of ParseCraft."""

from __future__ import annotations

from parsecraft.backends.errors import (
    BackendAlreadyRegisteredError,
    BackendError,
    BackendLoadError,
    BackendNotFoundError,
    DependencyUnavailableError,
    UnsupportedFormatError,
)
from parsecraft.backends.protocol import (
    GPU_NOT_NEEDED,
    GPU_OPTIONAL,
    GPU_REQUIRED,
    AnalysisResult,
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    BackendFactory,
    BackendRef,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
    ModelAssetDescriptor,
    PageSignal,
    SourceDocument,
)
from parsecraft.backends.registry import ENTRY_POINT_GROUP, BackendRegistry, default_registry

__all__ = [
    "ENTRY_POINT_GROUP",
    "GPU_NOT_NEEDED",
    "GPU_OPTIONAL",
    "GPU_REQUIRED",
    "AnalysisResult",
    "BackendAlreadyRegisteredError",
    "BackendCapabilities",
    "BackendConfig",
    "BackendDescriptor",
    "BackendError",
    "BackendFactory",
    "BackendLoadError",
    "BackendNotFoundError",
    "BackendRef",
    "BackendRegistry",
    "BackendResult",
    "ConversionRequest",
    "DependencyUnavailableError",
    "DocumentBackend",
    "UnsupportedFormatError",
    "ModelAssetDescriptor",
    "PageSignal",
    "SourceDocument",
    "default_registry",
]
