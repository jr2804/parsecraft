"""Generic configuration models: layers, provenance, and report shapes.

Self-contained: this module imports nothing from the rest of ParseCraft.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class ConfigLayer(StrEnum):
    """Ordered source of configuration values, lowest priority first."""

    DEFAULT = "default"
    GLOBAL = "global"
    PROJECT = "project"
    ENV = "env"
    CLI = "cli"


#: Layer precedence, lowest first, derived from declaration order.
LAYER_ORDER: tuple[ConfigLayer, ...] = tuple(ConfigLayer)


class _FrozenModel(BaseModel):
    """Base for immutable configuration value objects."""

    model_config = ConfigDict(frozen=True)


class ConfigSource(_FrozenModel):
    """Where one effective key came from."""

    layer: ConfigLayer
    origin: str


class ConfigWarning(_FrozenModel):
    """A non-fatal finding recorded while loading."""

    code: str
    message: str
    key: str | None = None


class ConfigCheckReport(_FrozenModel):
    """Result of validating the effective configuration."""

    valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[ConfigWarning, ...] = ()
    snapshot: dict[str, object] | None = None


class ConfigShowEntry(_FrozenModel):
    """One effective key with its value and provenance."""

    key: str
    value: object
    source: ConfigSource | None = None


class ConfigSchema(BaseSettings):
    """Base class for schemas validated by ``ConfigEngine``.

    Native pydantic-settings sources are disabled: the engine reads and orders
    all five layers itself so it can record per-key provenance. The schema's
    ``SettingsConfigDict`` still supplies ``env_prefix``, ``env_nested_delimiter``,
    and validation options.
    """

    model_config = SettingsConfigDict(extra="forbid")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Keep only init settings; the engine owns layer precedence."""
        return (init_settings,)
