"""Generic, self-contained configuration engine (Route A).

Public surface: :class:`ConfigEngine` plus the layer, provenance, and report
models. Nothing here imports another ParseCraft package.
"""

from __future__ import annotations

from parsecraft.config.engine import ConfigEngine
from parsecraft.config.errors import (
    ConfigError,
    ConfigFileError,
    ConfigLayerError,
    ConfigSecretError,
    ConfigValidationError,
)
from parsecraft.config.models import (
    LAYER_ORDER,
    ConfigCheckReport,
    ConfigLayer,
    ConfigSchema,
    ConfigShowEntry,
    ConfigSource,
    ConfigWarning,
)

__all__ = [
    "LAYER_ORDER",
    "ConfigCheckReport",
    "ConfigEngine",
    "ConfigError",
    "ConfigFileError",
    "ConfigLayer",
    "ConfigLayerError",
    "ConfigSchema",
    "ConfigSecretError",
    "ConfigShowEntry",
    "ConfigSource",
    "ConfigValidationError",
    "ConfigWarning",
]
