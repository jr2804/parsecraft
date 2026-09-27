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

from pydantic_settings import SettingsConfigDict

from parsecraft.cli.args import APP_NAME_UPPERCASE
from parsecraft.config import (
    ConfigCheckReport,
    ConfigEngine,
    ConfigError,
    ConfigLayer,
    ConfigSchema,
    ConfigShowEntry,
)

APP_NAME = "parsecraft"
PROJECT_CONFIG_FILENAME = "parsecraft.toml"
ENV_PREFIX = f"{APP_NAME_UPPERCASE}_CONFIG_"

#: Keys whose values ``config show`` redacts.
SECRET_KEYS: tuple[str, ...] = ("hf_token",)

#: Keys resolved relative to the file that declares them.
PATH_KEYS: tuple[str, ...] = ("cache_dir",)


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


def build_engine(config_file: Path | None = None) -> ConfigEngine[ParsecraftConfig]:
    """Assemble the layered engine: global file, project file, then env."""
    engine: ConfigEngine[ParsecraftConfig] = ConfigEngine(
        ParsecraftConfig,
        app_name=APP_NAME,
        path_keys=PATH_KEYS,
        secret_keys=SECRET_KEYS,
    )
    global_path = engine.global_config_path()
    if global_path.is_file():
        engine.read_file(ConfigLayer.GLOBAL, global_path)
    raw_project = config_file if config_file is not None else Path(PROJECT_CONFIG_FILENAME)
    project_path = raw_project if raw_project.is_absolute() else Path.cwd() / raw_project
    if project_path.is_file():
        engine.read_file(ConfigLayer.PROJECT, project_path)
    engine.read_env()
    return engine


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
            value="***" if entry.key in SECRET_KEYS else typed.get(entry.key, entry.value),
            source=entry.source,
        )
        for entry in entries
    ]


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
