"""Backend protocol + registry — the extension surface of ParseCraft."""

from __future__ import annotations

from parsecraft.backends.errors import (
    BackendAlreadyRegisteredError,
    BackendError,
    BackendLoadError,
    BackendNotFoundError,
    DependencyUnavailableError,
)
from parsecraft.backends.protocol import (
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
    "ModelAssetDescriptor",
    "PageSignal",
    "SourceDocument",
    "default_registry",
]
