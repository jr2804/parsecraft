"""Typed errors for the configuration engine.

Self-contained: this module imports nothing from the rest of ParseCraft.
"""

from __future__ import annotations


class ConfigError(Exception):
    """Base class for every configuration engine error."""


class ConfigLayerError(ConfigError):
    """Raised when values are attached to a layer that does not accept them."""

    def __init__(self, layer: str, allowed: str) -> None:
        super().__init__(f"layer {layer!r} cannot hold values here; allowed layers: {allowed}")
        self.layer = layer
        self.allowed = allowed


class ConfigFileError(ConfigError):
    """Raised when a TOML configuration file cannot be read or parsed."""

    def __init__(self, path: str, cause: Exception | None = None) -> None:
        detail = f": {type(cause).__name__}: {cause}" if cause is not None else ""
        super().__init__(f"cannot read config file {path!r}{detail}")
        self.path = path
        self.cause = cause


class ConfigSecretError(ConfigError):
    """Raised when a ``${VAR}`` reference names an unset environment variable."""

    def __init__(self, variable: str) -> None:
        super().__init__(f"environment variable {variable!r} referenced by a ${{VAR}} placeholder is not set")
        self.variable = variable


class ConfigValidationError(ConfigError):
    """Raised when the merged configuration fails schema validation."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
