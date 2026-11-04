"""Configuration diagnostic surface: schema, engine builder, and rendering.

The commands themselves stay plain functions in ``commands.py`` (see the CLI
contract in ``src/parsecraft/AGENTS.md``); this module holds the schema and the
pure rendering helpers they use.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import SettingsConfigDict

from parsecraft.backends import default_registry
from parsecraft.cli.args import APP_NAME_UPPERCASE
from parsecraft.config import (
    ConfigCheckReport,
    ConfigEngine,
    ConfigError,
    ConfigLayer,
    ConfigSchema,
    ConfigShowEntry,
    ConfigValidationError,
)
from parsecraft.routing.models import RoutingConstraints, RoutingPreference

APP_NAME = "parsecraft"
#: Home-relative global config directory (pc-66v): ``~/.config/parsecraft/``
#: on every platform. This deliberately overrides the engine's platformdirs
#: rule ("never a hard-coded XDG path") for this app's global layer.
GLOBAL_CONFIG_DIRNAME = ".config"
GLOBAL_CONFIG_FILENAME = "config.toml"
PROJECT_CONFIG_FILENAME = "parsecraft.toml"
ENV_PREFIX = f"{APP_NAME_UPPERCASE}_CONFIG_"

#: Keys whose values ``config show`` redacts.
SECRET_KEYS: tuple[str, ...] = ("hf_token",)

#: Keys resolved relative to the file that declares them.
PATH_KEYS: tuple[str, ...] = ("cache_dir",)


class ProviderEndpoint(BaseModel):
    """One judge provider's endpoint: base URL and credential (pc-66v).

    The section name is the provider token (``[providers.zen]``) or a
    consumer-style alias (``[providers.typesafe]`` means ``typesafe-ai``,
    ``[providers.ollama-cloud]`` means ``ollama``). ``api_key`` must reference
    a secret (``api_key = "${TYPESAFE_API_KEY}"``), never a literal — the
    config engine refuses literal secrets the same way it refuses literal
    ``hf_token`` values.
    """

    model_config = SettingsConfigDict(extra="forbid")

    base_url: str | None = None
    api_key: str | None = None


class JudgeSettings(BaseModel):
    """The default decision model when no ``--judge`` spec is given (pc-66v)."""

    model_config = SettingsConfigDict(extra="forbid")

    model: str | None = None


class TextClassifierSettings(BaseModel):
    """The text-kind classifier model, independent of routing (pc-9p6.5).

    A dedicated activation surface: neither ``[judge] model`` nor the routing
    judge object may switch on document shaping, so classification engages
    only through this section (no CLI flag — the extract only reads
    configuration).
    """

    model_config = SettingsConfigDict(extra="forbid")

    model: str | None = None


class RoutingSettings(BaseModel):
    """User-settable routing knobs, mapped 1:1 onto ``RoutingConstraints`` (pc-8qc).

    This section is the single source of truth for backend/routing settings,
    so consumers no longer carry their own ``parsecraft_*`` copies.
    Deliberately ABSENT (derived per call by the consumer, never configured):
    ``formats`` (from the source media type), ``installed_extras`` (from the
    registry), ``cached_assets`` (from the run), and ``offline`` (already a
    top-level key).

    Semantics that must survive into :meth:`routing_constraints`:

    - ``allow_ocr = None`` (the key absent) means "no override — let the
      eligibility derivation decide"; it is distinguishable from ``false``
      (TOML booleans only bind ``true``/``false``, so an explicit false never
      collapses into None).
    - ``preferred_backend``/``only_backend`` normalize ``""`` to ``None`` (an
      empty name is no name); this normalization is deliberate and documented.
    - ``backend_options`` values stay scalar and are forwarded VERBATIM into
      ``BackendConfig.options`` (they are part of the conversion-cache key).
      Option KEYS are backend-owned and validated at first conversion, not
      here (pc-k92 — a registry capability; a typo surfaces as a typed
      BACKEND_ERROR naming backend and key).
    """

    model_config = SettingsConfigDict(extra="forbid")

    preference: RoutingPreference = RoutingPreference.BALANCED
    allow_ocr: bool | None = None
    engines: list[str] = []
    preferred_backend: str | None = None
    only_backend: str | None = None
    backend_options: dict[str, dict[str, str | int | float | bool]] = {}
    language: str | None = None
    max_passes: int = Field(default=1, ge=1)
    vram_budget_gb: float = Field(default=0.0, ge=0)
    gpu_usable: bool = False

    def routing_constraints(
        self,
        *,
        installed_extras: set[str],
        formats: set[str],
        cached_assets: frozenset[str] = frozenset(),
        offline: bool | None = None,
    ) -> RoutingConstraints:
        """Build a valid ``RoutingConstraints`` from this section (pc-8qc).

        The one public construction path for config-driven routing, so a
        library consumer never re-derives the mapping. ``offline=None`` (the
        default) leaves ``RoutingConstraints``' own default (``True``: no
        downloads); pass the top-level ``offline`` value for the usual
        mapping. Unknown backend names (``preferred_backend``/``only_backend``)
        and unknown engine ids (``engines``) raise :class:`ConfigError` naming
        the key and the accepted set — never a silent fallback.
        """
        backend_names, engine_ids = _registered_names()
        _check_names("preferred_backend", self.preferred_backend, backend_names)
        _check_names("only_backend", self.only_backend, backend_names)
        for engine in self.engines:
            if engine not in engine_ids:
                raise ConfigError(_unknown_name_error("routing.engines", engine, sorted(engine_ids)))
        kwargs: dict[str, object] = {
            "formats": formats,
            "installed_extras": installed_extras,
            "cached_assets": cached_assets,
            "engines": frozenset(self.engines),
            "max_passes": self.max_passes,
            "vram_budget_gb": self.vram_budget_gb,
            "gpu_usable": self.gpu_usable,
            "language": self.language,
            "preference": self.preference,
            "backend_options": self.backend_options,
            "preferred_backend": self.preferred_backend or None,
            "only_backend": self.only_backend or None,
        }
        if self.allow_ocr is not None:
            kwargs["allow_ocr"] = self.allow_ocr
        if offline is not None:
            kwargs["offline"] = offline
        try:
            return RoutingConstraints.model_validate(kwargs)
        except ValidationError as exc:
            message = f"[routing] failed validation: {exc}"
            raise ConfigValidationError(message) from exc


class ParsecraftConfig(ConfigSchema):
    """Settings recognized by the ParseCraft CLI and its components.

    ``hf_token`` is secret-bearing: write it as ``${HF_TOKEN}``, never a
    literal. ``cache_dir``, ``offline``, and ``min_free_bytes`` mirror the
    ``AssetManager`` constructor. The env prefix is ``PARSECRAFT_CONFIG_`` so
    the CLI's own ``PARSECRAFT_JSON`` flag cannot collide with settings.
    """

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_nested_delimiter="__",
        extra="forbid",
    )

    hf_token: str | None = None
    cache_dir: str | None = None
    offline: bool = False
    min_free_bytes: int = 0
    timeout_s: float | None = Field(default=None, gt=0)
    providers: dict[str, ProviderEndpoint] = {}
    judge: JudgeSettings = JudgeSettings()
    text_classifier: TextClassifierSettings = TextClassifierSettings()
    routing: RoutingSettings = RoutingSettings()


def _check_names(key: str, value: str | None, backend_names: set[str]) -> None:
    if value and value not in backend_names:
        raise ConfigError(_unknown_name_error(f"routing.{key}", value, sorted(backend_names)))


def build_engine(config_files: Sequence[Path] | None = None) -> ConfigEngine[ParsecraftConfig]:
    """Assemble the layered engine: global file, project files, then env.

    Layer priority, lowest to highest (pc-8qc; a later file wins per key and
    every key's provenance names the file it came from):

    1. schema defaults
    2. global   ``~/.config/parsecraft/config.toml``
    3. vault    ``<vault>/parsecraft.toml``            (consumer-supplied)
    4. project  ``./parsecraft.toml``                  (cwd, CLI convenience)
    5. env      ``PARSECRAFT_CONFIG_*``
    6. CLI flags

    ``config_files`` are the PROJECT-layer files in priority order (item 3
    before item 4); ``None`` falls back to the cwd project file alone, while
    an empty sequence means no project files at all. Relative paths resolve
    against the current directory. Missing files are skipped silently.
    """
    engine: ConfigEngine[ParsecraftConfig] = ConfigEngine(
        ParsecraftConfig,
        app_name=APP_NAME,
        path_keys=PATH_KEYS,
        secret_keys=SECRET_KEYS,
    )
    global_path = global_config_path()
    if global_path.is_file():
        engine.read_file(ConfigLayer.GLOBAL, global_path)
    files = config_files if config_files is not None else [Path(PROJECT_CONFIG_FILENAME)]
    paths = [path if path.is_absolute() else Path.cwd() / path for path in files]
    present = [path for path in paths if path.is_file()]
    if present:
        engine.read_files(ConfigLayer.PROJECT, present)
    engine.read_env()
    return engine


def routing_settings_errors(settings: RoutingSettings) -> list[str]:
    """Load-time backend/engine name errors for ``config check`` (pc-8qc).

    The same loudness :meth:`RoutingSettings.routing_constraints` applies to
    library callers, reported through the check instead of raised. Option
    keys are NOT validated here — they are backend-owned and surface at
    first conversion (pc-k92).
    """
    backend_names, engine_ids = _registered_names()
    errors: list[str] = []
    for key, value in (("preferred_backend", settings.preferred_backend), ("only_backend", settings.only_backend)):
        if value and value not in backend_names:
            errors.append(_unknown_name_error(f"routing.{key}", value, sorted(backend_names)))
    for engine in settings.engines:
        if engine not in engine_ids:
            errors.append(_unknown_name_error("routing.engines", engine, sorted(engine_ids)))
    return errors


def _registered_names() -> tuple[set[str], set[str]]:
    """Backend names and declared engine ids the registry knows about."""
    descriptors = default_registry.list_backends()
    backend_names = {descriptor.name for descriptor in descriptors}
    engine_ids = {descriptor.capabilities.required_engine for descriptor in descriptors if descriptor.capabilities.required_engine is not None}
    return backend_names, engine_ids


def _unknown_name_error(key: str, value: str, accepted: list[str]) -> str:
    return f"{key}: unknown name {value!r}; accepted: {', '.join(accepted)}"


def global_config_path() -> Path:
    """Platform-independent global config path (pc-66v exception, see above)."""
    return Path.home() / GLOBAL_CONFIG_DIRNAME / APP_NAME / GLOBAL_CONFIG_FILENAME


def resolved_entries(engine: ConfigEngine[ParsecraftConfig]) -> list[ConfigShowEntry]:
    """Provenance entries with schema-coerced values when the config is valid.

    ``ConfigEngine.show`` reports source-level values (env values stay strings);
    the effective configuration is the validated one, so the CLI overlays the
    typed values from ``load()`` and re-applies secret redaction.
    """
    entries = engine.show()
    try:
        typed = flatten_values(engine.load().model_dump(mode="json"))
    except ConfigError:
        return entries
    return [
        ConfigShowEntry(
            key=entry.key,
            value="***" if _is_secret_key(entry.key) else typed.get(entry.key, entry.value),
            source=entry.source,
        )
        for entry in entries
    ]


def _is_secret_key(key: str) -> bool:
    """Exact secrets plus every provider's ``api_key`` (section names vary)."""
    return key in SECRET_KEYS or (key.startswith("providers.") and key.endswith(".api_key"))


def check_lines(report: ConfigCheckReport) -> list[tuple[bool, str]]:
    """Return ``(is_error, line)`` pairs for ``config check`` text output."""
    status = "Configuration is valid." if report.valid else "Configuration is invalid."
    lines: list[tuple[bool, str]] = [(not report.valid, status)]
    lines.extend((True, f"warning [{warning.code}] {warning.message}") for warning in report.warnings)
    lines.extend((True, f"error: {error}") for error in report.errors)
    return lines


def show_lines(entries: Sequence[ConfigShowEntry]) -> list[str]:
    """Return one ``key = value  (layer:origin)`` line per entry."""
    return [f"{entry.key} = {json.dumps(entry.value)}  ({_source_label(entry)})" for entry in entries]


def flatten_values(values: Mapping[str, object], prefix: str = "") -> dict[str, object]:
    """Flatten nested mappings to dotted-key leaves."""
    flat: dict[str, object] = {}
    for key in sorted(values):
        value = values[key]
        dotted = f"{prefix}{key}"
        if type(value) is dict:
            flat.update(flatten_values(cast("Mapping[str, object]", value), f"{dotted}."))
        else:
            flat[dotted] = value
    return flat


def _source_label(entry: ConfigShowEntry) -> str:
    source = entry.source
    if source is None:
        return "unset"
    return f"{source.layer.value}:{source.origin}"
