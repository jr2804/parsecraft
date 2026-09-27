"""Generic, layered, provenance-tracking configuration engine (Route A).

This package is self-contained: it imports nothing from the rest of ParseCraft,
so it can be split into its own distribution or vendored by a consumer.

Layer precedence, lowest first:

    default -> global -> project -> env -> cli

Values from later layers override earlier ones. Every effective leaf key keeps a
``ConfigSource`` record, so ``source_of("section.key")`` answers where a value
came from. ``${VAR}`` placeholders resolve from the environment and never hold
literal secrets in files.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TypeVar, cast

import platformdirs
import tomlkit
from pydantic import BaseModel, ValidationError
from tomlkit.exceptions import ParseError

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

_SECRET_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_DEFAULT_ENV_DELIMITER = "__"
_GLOBAL_CONFIG_FILENAME = "config.toml"
_DEFAULT_ORIGIN = "<defaults>"

#: Layers whose origin is a file on disk, so relative paths resolve next to it.
_FILE_LAYERS: tuple[ConfigLayer, ...] = (ConfigLayer.GLOBAL, ConfigLayer.PROJECT)

SchemaT = TypeVar("SchemaT", bound=ConfigSchema)


class ConfigEngine[SchemaT: ConfigSchema]:
    """Load and validate a :class:`ConfigSchema` from ordered layers.

    The engine never reads the process environment implicitly through the
    schema: ``read_env()`` records the env layer explicitly, which is what
    preserves per-key provenance and keeps CLI overrides on top.
    """

    def __init__(
        self,
        schema: type[SchemaT],
        *,
        app_name: str,
        path_keys: Iterable[str] = (),
        env_aliases: Mapping[str, str] | None = None,
        deprecated_aliases: Mapping[str, str] | None = None,
        secret_keys: Iterable[str] = (),
        environ: Mapping[str, str] | None = None,
    ) -> None:
        """Create an engine for ``schema`` under ``app_name``.

        ``path_keys`` are dotted keys resolved relative to their declaring file;
        ``env_aliases`` maps dotted keys to explicit environment variable names;
        ``deprecated_aliases`` maps legacy keys to their replacements;
        ``secret_keys`` are redacted by :meth:`show`.
        """
        self._schema = schema
        self._app_name = app_name
        self._path_keys = frozenset(path_keys)
        self._env_aliases = dict(env_aliases or {})
        self._deprecated_aliases = dict(deprecated_aliases or {})
        self._secret_keys = frozenset(secret_keys)
        self._environ = environ
        self._values: dict[ConfigLayer, dict[str, object]] = {ConfigLayer.DEFAULT: _plain_mapping(_schema_defaults(schema))}
        self._origins: dict[ConfigLayer, str] = {ConfigLayer.DEFAULT: _DEFAULT_ORIGIN}
        self._warnings: list[ConfigWarning] = []

    # ── Recording layers ──────────────────────────────────────────────────

    def set_defaults(self, values: Mapping[str, object]) -> None:
        """Merge values into the ``default`` layer (lowest priority)."""
        current = self._values.get(ConfigLayer.DEFAULT, {})
        self._record(ConfigLayer.DEFAULT, _deep_update(current, _plain_mapping(values)), origin=_DEFAULT_ORIGIN)

    def read_file(self, layer: ConfigLayer, path: str | Path) -> None:
        """Read a TOML file into ``global`` or ``project`` and migrate aliases."""
        if layer not in _FILE_LAYERS:
            allowed = ", ".join(item.value for item in _FILE_LAYERS)
            raise ConfigLayerError(layer.value, allowed)
        file_path = Path(path)
        try:
            document = tomlkit.parse(file_path.read_text(encoding="utf-8"))
        except (OSError, ParseError) as exc:
            raise ConfigFileError(str(file_path), exc) from exc
        values = _plain_mapping(cast("Mapping[str, object]", document.unwrap()))
        self._record(layer, self._migrate_aliases(values), origin=str(file_path))

    def read_env(self) -> None:
        """Record the ``env`` layer from the configured environment mapping."""
        environ = self._environment()
        prefix = str(self._schema.model_config.get("env_prefix") or f"{self._app_name.upper()}_")
        delimiter = str(self._schema.model_config.get("env_nested_delimiter") or _DEFAULT_ENV_DELIMITER)
        alias_by_env = {env_name: key for key, env_name in self._env_aliases.items()}
        values: dict[str, object] = {}
        for name, raw in environ.items():
            dotted = alias_by_env.get(name)
            if dotted is None:
                if not name.startswith(prefix):
                    continue
                dotted = name[len(prefix) :].lower().replace(delimiter, ".")
            _dotted_set(values, dotted, raw)
        self._record(ConfigLayer.ENV, values, origin="<environment>")

    def set_cli(self, values: Mapping[str, object]) -> None:
        """Record the ``cli`` layer (highest priority); keys may be dotted."""
        flattened: dict[str, object] = {}
        for key, value in values.items():
            _dotted_set(flattened, key, _plain(value))
        self._record(ConfigLayer.CLI, flattened, origin="<command line>")

    # ── Derived outputs ───────────────────────────────────────────────────

    @property
    def warnings(self) -> tuple[ConfigWarning, ...]:
        """Non-fatal findings recorded so far, in load order."""
        return tuple(self._warnings)

    def source_of(self, key: str) -> ConfigSource | None:
        """Provenance for one dotted key, or ``None`` when the key is unset."""
        _, provenance = self._effective()
        return provenance.get(key)

    def load(self) -> SchemaT:
        """Merge, resolve secrets and paths, then validate the schema."""
        values, _ = self._resolved_values()
        try:
            return self._schema.model_validate(values)
        except ValidationError as exc:
            raise ConfigValidationError(_format_validation_error(exc)) from exc

    def snapshot(self) -> dict[str, object]:
        """Deterministic, key-sorted effective configuration."""
        values, _ = self._resolved_values()
        return _sorted_mapping(values)

    def check(self) -> ConfigCheckReport:
        """Validate and return a report instead of raising."""
        warnings = tuple(self._warnings)
        try:
            settings = self.load()
        except ConfigError as exc:
            return ConfigCheckReport(valid=False, errors=(str(exc),), warnings=warnings)
        snapshot = _sorted_mapping(cast("dict[str, object]", settings.model_dump(mode="json")))
        return ConfigCheckReport(valid=True, warnings=warnings, snapshot=snapshot)

    def show(self) -> list[ConfigShowEntry]:
        """Every effective leaf key with value and provenance, sorted by key."""
        values, provenance = self._resolved_values()
        entries = [
            ConfigShowEntry(
                key=key,
                value="***" if key in self._secret_keys else value,
                source=provenance.get(key),
            )
            for key, value in _flatten(values)
        ]
        return sorted(entries, key=lambda entry: entry.key)

    def global_config_path(self) -> Path:
        """Platform-appropriate global config path (never a hard-coded XDG path)."""
        return Path(platformdirs.user_config_dir(self._app_name)) / _GLOBAL_CONFIG_FILENAME

    def migrate(self, path: str | Path) -> bool:
        """Rewrite deprecated keys in a TOML file, preserving its style."""
        file_path = Path(path)
        try:
            document = tomlkit.parse(file_path.read_text(encoding="utf-8"))
        except (OSError, ParseError) as exc:
            raise ConfigFileError(str(file_path), exc) from exc
        changed = False
        for old, new in self._deprecated_aliases.items():
            if old not in document or new in document:
                continue
            document[new] = document.pop(old)
            changed = True
        if changed:
            file_path.write_text(tomlkit.dumps(document), encoding="utf-8")
        return changed

    # ── Internals ─────────────────────────────────────────────────────────

    def _environment(self) -> Mapping[str, str]:
        return self._environ if self._environ is not None else os.environ

    def _record(self, layer: ConfigLayer, values: Mapping[str, object], *, origin: str) -> None:
        self._values[layer] = _plain_mapping(values)
        self._origins[layer] = origin

    def _migrate_aliases(self, values: dict[str, object]) -> dict[str, object]:
        for old, new in self._deprecated_aliases.items():
            if old not in values:
                continue
            if new in values:
                self._warnings.append(
                    ConfigWarning(
                        code="deprecated_alias_shadowed",
                        message=f"deprecated key {old!r} ignored: {new!r} is already set",
                        key=old,
                    )
                )
                del values[old]
                continue
            values[new] = values.pop(old)
            self._warnings.append(
                ConfigWarning(
                    code="deprecated_alias",
                    message=f"key {old!r} is deprecated; use {new!r}",
                    key=old,
                )
            )
        return values

    def _effective(self) -> tuple[dict[str, object], dict[str, ConfigSource]]:
        merged: dict[str, object] = {}
        provenance: dict[str, ConfigSource] = {}
        for layer in LAYER_ORDER:
            values = self._values.get(layer)
            if not values:
                continue
            source = ConfigSource(layer=layer, origin=self._origins[layer])
            _merge_into(merged, values, prefix="", provenance=provenance, source=source)
        return merged, provenance

    def _resolved_values(self) -> tuple[dict[str, object], dict[str, ConfigSource]]:
        merged, provenance = self._effective()
        expanded = _expand_secrets(merged, self._environment())
        values = cast("dict[str, object]", expanded)
        self._resolve_paths(values, provenance)
        return values, provenance

    def _resolve_paths(self, values: dict[str, object], provenance: dict[str, ConfigSource]) -> None:
        for key, current in _flatten(values):
            if key not in self._path_keys:
                continue
            source = provenance.get(key)
            if source is None or source.layer not in _FILE_LAYERS:
                continue
            if type(current) is not str:
                continue
            candidate = Path(current)
            if candidate.is_absolute():
                continue
            _dotted_set(values, key, str(Path(source.origin).parent / candidate))


def _schema_defaults(schema: type[ConfigSchema]) -> dict[str, object]:
    defaults: dict[str, object] = {}
    for name, field in schema.model_fields.items():
        if field.is_required():
            continue
        value = field.get_default(call_default_factory=True)
        defaults[name] = value.model_dump() if isinstance(value, BaseModel) else value
    return defaults


def _deep_update(target: dict[str, object], values: Mapping[str, object]) -> dict[str, object]:
    for key, value in values.items():
        current = target.get(key)
        if type(value) is dict and type(current) is dict:
            _deep_update(cast("dict[str, object]", current), cast("Mapping[str, object]", value))
            continue
        target[key] = value
    return target


def _merge_into(
    target: dict[str, object],
    values: Mapping[str, object],
    *,
    prefix: str,
    provenance: dict[str, ConfigSource],
    source: ConfigSource,
) -> None:
    for key, value in values.items():
        dotted = f"{prefix}{key}"
        if type(value) is dict:
            child = target.get(key)
            if type(child) is not dict:
                child = {}
                target[key] = child
                provenance.pop(dotted, None)
            _merge_into(
                cast("dict[str, object]", child),
                cast("Mapping[str, object]", value),
                prefix=f"{dotted}.",
                provenance=provenance,
                source=source,
            )
            continue
        if type(target.get(key)) is dict:
            _drop_provenance_prefix(provenance, f"{dotted}.")
        target[key] = value
        provenance[dotted] = source


def _expand_secrets(value: object, environ: Mapping[str, str]) -> object:
    if type(value) is dict:
        mapping = cast("Mapping[str, object]", value)
        return {key: _expand_secrets(item, environ) for key, item in mapping.items()}
    if type(value) is list:
        return [_expand_secrets(item, environ) for item in cast("list[object]", value)]
    if type(value) is str:
        return _SECRET_PATTERN.sub(lambda match: _lookup_secret(match.group(1), environ), value)
    return value


def _lookup_secret(name: str, environ: Mapping[str, str]) -> str:
    try:
        return environ[name]
    except KeyError as exc:
        raise ConfigSecretError(name) from exc


def _dotted_set(values: dict[str, object], key: str, value: object) -> None:
    parts = key.split(".")
    current = values
    for part in parts[:-1]:
        child = current.get(part)
        if type(child) is not dict:
            child = {}
            current[part] = child
        current = cast("dict[str, object]", child)
    current[parts[-1]] = value


def _drop_provenance_prefix(provenance: dict[str, ConfigSource], prefix: str) -> None:
    for key in [item for item in provenance if item.startswith(prefix)]:
        del provenance[key]


def _sorted_mapping(values: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key in sorted(values):
        value = values[key]
        if type(value) is dict:
            result[key] = _sorted_mapping(cast("Mapping[str, object]", value))
        elif type(value) is list:
            result[key] = [_sorted_mapping(cast("Mapping[str, object]", item)) if type(item) is dict else item for item in cast("list[object]", value)]
        else:
            result[key] = value
    return result


def _flatten(values: Mapping[str, object], prefix: str = "") -> list[tuple[str, object]]:
    items: list[tuple[str, object]] = []
    for key in sorted(values):
        value = values[key]
        dotted = f"{prefix}{key}"
        if type(value) is dict:
            items.extend(_flatten(cast("Mapping[str, object]", value), f"{dotted}."))
        else:
            items.append((dotted, value))
    return items


def _format_validation_error(error: ValidationError) -> str:
    lines = ["configuration failed validation:"]
    for detail in error.errors():
        location = ".".join(str(part) for part in detail["loc"]) or "<root>"
        lines.append(f"  {location}: {detail['msg']}")
    return "\n".join(lines)


def _plain(value: object) -> object:
    if type(value) is dict:
        return _plain_mapping(cast("Mapping[str, object]", value))
    if type(value) is list:
        return [_plain(item) for item in cast("list[object]", value)]
    return value


def _plain_mapping(values: Mapping[str, object]) -> dict[str, object]:
    return {str(key): _plain(item) for key, item in values.items()}
