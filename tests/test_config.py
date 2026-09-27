"""Tests for the generic configuration engine (Route A)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import platformdirs
import pytest
from pydantic import BaseModel, Field
from pydantic_settings import SettingsConfigDict

from parsecraft.config import (
    LAYER_ORDER,
    ConfigCheckReport,
    ConfigEngine,
    ConfigError,
    ConfigFileError,
    ConfigLayer,
    ConfigLayerError,
    ConfigSchema,
    ConfigSecretError,
    ConfigShowEntry,
    ConfigSource,
    ConfigValidationError,
    ConfigWarning,
)


class LogConfig(BaseModel):
    """Nested section used to exercise nested TOML and env mapping."""

    level: str = "info"
    file: str = "app.log"


class Demo(ConfigSchema):
    """Schema with an env prefix, nested delimiter, and strict extras."""

    model_config = SettingsConfigDict(env_prefix="PARSECRAFT_", env_nested_delimiter="__", extra="forbid")

    name: str = "demo"
    workers: int = 1
    debug: bool = False
    data_dir: str = "data"
    tags: list[str] = Field(default_factory=list)
    items: list[dict[str, int]] = Field(default_factory=list)
    log: LogConfig = Field(default_factory=LogConfig)


class Plain(ConfigSchema):
    """Schema without an env prefix or nested delimiter (fallback path)."""

    name: str = "plain"
    log: LogConfig = Field(default_factory=LogConfig)


class Required(ConfigSchema):
    """Schema with a required field, used for validation errors."""

    host: str


def test_layer_order_is_ascending() -> None:
    assert LAYER_ORDER == (
        ConfigLayer.DEFAULT,
        ConfigLayer.GLOBAL,
        ConfigLayer.PROJECT,
        ConfigLayer.ENV,
        ConfigLayer.CLI,
    )


def test_snapshot_is_deterministic_and_key_sorted() -> None:
    engine = ConfigEngine(Demo, app_name="parsecraft")
    engine.set_defaults({"name": "x", "log": {"file": "z.log"}, "tags": ["b", "a"], "items": [{"z": 1, "a": 2}]})
    snapshot = engine.snapshot()
    assert list(snapshot) == ["data_dir", "debug", "items", "log", "name", "tags", "workers"]
    assert list(cast("dict[str, object]", snapshot["log"])) == ["file", "level"]
    items = cast("list[object]", snapshot["items"])
    assert list(cast("dict[str, object]", items[0])) == ["a", "z"]
    assert snapshot == engine.snapshot()


def test_layer_precedence_and_provenance(tmp_path: Path) -> None:
    global_path = _write(tmp_path / "global.toml", 'name = "from-global"\nworkers = 2\n')
    project_path = _write(tmp_path / "project.toml", 'name = "from-project"\n')
    engine = ConfigEngine(Demo, app_name="parsecraft", environ={"PARSECRAFT_NAME": "from-env"})
    engine.set_defaults({"name": "from-default"})
    engine.read_file(ConfigLayer.GLOBAL, global_path)
    engine.read_file(ConfigLayer.PROJECT, project_path)
    engine.read_env()
    assert engine.load().name == "from-env"
    assert engine.source_of("name") == ConfigSource(layer=ConfigLayer.ENV, origin="<environment>")
    assert engine.source_of("workers") == ConfigSource(layer=ConfigLayer.GLOBAL, origin=str(global_path))
    engine.set_cli({"name": "from-cli"})
    assert engine.load().name == "from-cli"
    assert engine.source_of("name") == ConfigSource(layer=ConfigLayer.CLI, origin="<command line>")


def test_source_of_unknown_key_is_none() -> None:
    assert ConfigEngine(Demo, app_name="parsecraft").source_of("missing") is None


def test_provenance_pruned_when_shape_changes() -> None:
    engine = ConfigEngine(Demo, app_name="parsecraft")
    engine.set_defaults({"log": {"level": "debug"}})
    engine.set_cli({"log": "plain"})
    assert engine.source_of("log.level") is None
    assert engine.source_of("log") == ConfigSource(layer=ConfigLayer.CLI, origin="<command line>")

    engine.set_defaults({"log": "scalar"})
    engine.set_cli({"log": {"level": "trace"}})
    assert engine.source_of("log") is None
    assert engine.source_of("log.level") == ConfigSource(layer=ConfigLayer.CLI, origin="<command line>")


def test_file_relative_path_resolution(tmp_path: Path) -> None:
    project_path = _write(tmp_path / "project.toml", 'data_dir = "assets"\n')
    engine = ConfigEngine(Demo, app_name="parsecraft", path_keys=("data_dir",))
    engine.read_file(ConfigLayer.PROJECT, project_path)
    assert engine.load().data_dir == str(tmp_path / "assets")


def test_absolute_and_non_file_paths_are_left_alone(tmp_path: Path) -> None:
    absolute = str(tmp_path / "abs")
    engine = ConfigEngine(Demo, app_name="parsecraft", path_keys=("data_dir", "workers"))
    engine.set_defaults({"workers": 2})
    engine.set_cli({"data_dir": absolute})
    assert engine.load().data_dir == absolute

    file_path = _write(tmp_path / "project.toml", f"data_dir = {json.dumps(absolute)}\n")
    file_engine = ConfigEngine(Demo, app_name="parsecraft", path_keys=("data_dir",))
    file_engine.read_file(ConfigLayer.PROJECT, file_path)
    assert file_engine.load().data_dir == absolute


def test_file_path_key_with_non_string_value_is_ignored(tmp_path: Path) -> None:
    path = _write(tmp_path / "project.toml", "workers = 3\n")
    engine = ConfigEngine(Demo, app_name="parsecraft", path_keys=("workers",))
    engine.read_file(ConfigLayer.PROJECT, path)
    assert engine.load().workers == 3


def test_secret_placeholders_resolve_and_missing_raises() -> None:
    engine = ConfigEngine(Demo, app_name="parsecraft", environ={"TOKEN": "s3cret", "WHO": "world"})
    engine.set_defaults({"name": "hello ${WHO}", "tags": ["${TOKEN}"]})
    settings = engine.load()
    assert settings.name == "hello world"
    assert settings.tags == ["s3cret"]

    missing = ConfigEngine(Demo, app_name="parsecraft", environ={})
    missing.set_defaults({"name": "${NOPE}"})
    with pytest.raises(ConfigSecretError, match="NOPE"):
        missing.load()


def test_env_layer_uses_prefix_delimiter_and_aliases() -> None:
    engine = ConfigEngine(
        Demo,
        app_name="parsecraft",
        env_aliases={"workers": "CUSTOM_WORKERS"},
        environ={"CUSTOM_WORKERS": "7", "PARSECRAFT_LOG__LEVEL": "debug", "UNRELATED": "x"},
    )
    engine.read_env()
    settings = engine.load()
    assert settings.workers == 7
    assert settings.log.level == "debug"


def test_env_fallback_prefix_and_delimiter() -> None:
    engine = ConfigEngine(Plain, app_name="parsecraft", environ={"PARSECRAFT_NAME": "from-plain", "PARSECRAFT_LOG__LEVEL": "warn"})
    engine.read_env()
    settings = engine.load()
    assert settings.name == "from-plain"
    assert settings.log.level == "warn"


def test_deprecated_alias_absent_is_ignored(tmp_path: Path) -> None:
    path = _write(tmp_path / "project.toml", "workers = 4\n")
    engine = ConfigEngine(Demo, app_name="parsecraft", deprecated_aliases={"timeout": "workers"})
    engine.read_file(ConfigLayer.PROJECT, path)
    assert engine.warnings == ()
    assert engine.load().workers == 4


def test_deprecated_alias_records_warning(tmp_path: Path) -> None:
    path = _write(tmp_path / "project.toml", "timeout = 5\n")
    engine = ConfigEngine(Demo, app_name="parsecraft", deprecated_aliases={"timeout": "workers"})
    engine.read_file(ConfigLayer.PROJECT, path)
    assert engine.load().workers == 5
    assert engine.warnings == (ConfigWarning(code="deprecated_alias", message="key 'timeout' is deprecated; use 'workers'", key="timeout"),)


def test_deprecated_alias_shadowed_records_warning(tmp_path: Path) -> None:
    path = _write(tmp_path / "project.toml", "timeout = 5\nworkers = 9\n")
    engine = ConfigEngine(Demo, app_name="parsecraft", deprecated_aliases={"timeout": "workers"})
    engine.read_file(ConfigLayer.PROJECT, path)
    assert engine.load().workers == 9
    assert engine.warnings[0].code == "deprecated_alias_shadowed"
    assert engine.warnings[0].key == "timeout"


def test_migrate_rewrites_file_once(tmp_path: Path) -> None:
    path = _write(tmp_path / "project.toml", "timeout = 5\n")
    engine = ConfigEngine(Demo, app_name="parsecraft", deprecated_aliases={"timeout": "workers"})
    assert engine.migrate(path) is True
    assert path.read_text(encoding="utf-8") == "workers = 5\n"
    assert engine.migrate(path) is False


def test_migrate_skips_shadowed_alias(tmp_path: Path) -> None:
    path = _write(tmp_path / "project.toml", "timeout = 5\nworkers = 9\n")
    engine = ConfigEngine(Demo, app_name="parsecraft", deprecated_aliases={"timeout": "workers"})
    assert engine.migrate(path) is False
    assert path.read_text(encoding="utf-8") == "timeout = 5\nworkers = 9\n"


def test_read_file_rejects_non_file_layer(tmp_path: Path) -> None:
    engine = ConfigEngine(Demo, app_name="parsecraft")
    with pytest.raises(ConfigLayerError) as error:
        engine.read_file(ConfigLayer.ENV, tmp_path / "x.toml")
    assert error.value.layer == "env"
    assert error.value.allowed == "global, project"


def test_read_file_reports_missing_and_invalid_toml(tmp_path: Path) -> None:
    engine = ConfigEngine(Demo, app_name="parsecraft")
    with pytest.raises(ConfigFileError, match="cannot read config file"):
        engine.read_file(ConfigLayer.PROJECT, tmp_path / "missing.toml")

    invalid = _write(tmp_path / "invalid.toml", "name = \n")
    with pytest.raises(ConfigFileError) as error:
        engine.read_file(ConfigLayer.PROJECT, invalid)
    assert error.value.cause is not None


def test_migrate_reports_missing_file(tmp_path: Path) -> None:
    engine = ConfigEngine(Demo, app_name="parsecraft", deprecated_aliases={"timeout": "workers"})
    with pytest.raises(ConfigFileError, match="cannot read config file"):
        engine.migrate(tmp_path / "missing.toml")


def test_check_reports_valid_snapshot(tmp_path: Path) -> None:
    path = _write(tmp_path / "project.toml", "timeout = 3\n")
    engine = ConfigEngine(Demo, app_name="parsecraft", deprecated_aliases={"timeout": "workers"})
    engine.read_file(ConfigLayer.PROJECT, path)
    report = engine.check()
    assert report.valid is True
    assert report.errors == ()
    assert report.warnings[0].code == "deprecated_alias"
    assert report.snapshot is not None
    assert report.snapshot["workers"] == 3


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_check_reports_validation_failure() -> None:
    engine = ConfigEngine(Demo, app_name="parsecraft", environ={"PARSECRAFT_WORKERS": "not-a-number"})
    engine.read_env()
    report = engine.check()
    assert report.valid is False
    assert report.snapshot is None
    assert len(report.errors) == 1
    assert "configuration failed validation" in report.errors[0]


def test_show_redacts_and_sorts() -> None:
    engine = ConfigEngine(Demo, app_name="parsecraft", secret_keys=("name",))
    engine.set_defaults({"log": {"level": "debug"}, "name": "secret"})
    entries = engine.show()
    assert [entry.key for entry in entries] == ["data_dir", "debug", "items", "log.file", "log.level", "name", "tags", "workers"]
    by_key = {entry.key: entry for entry in entries}
    assert by_key["name"].value == "***"
    assert by_key["name"].source == ConfigSource(layer=ConfigLayer.DEFAULT, origin="<defaults>")
    assert by_key["log.level"].value == "debug"


def test_global_config_path_uses_platformdirs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platformdirs, "user_config_dir", lambda _app: str(Path("/config-root")))
    engine = ConfigEngine(Demo, app_name="parsecraft")
    assert engine.global_config_path() == Path("/config-root/config.toml")


def test_cli_accepts_dotted_keys() -> None:
    engine = ConfigEngine(Demo, app_name="parsecraft")
    engine.set_cli({"log.level": "trace"})
    assert engine.load().log.level == "trace"


def test_validation_error_names_the_field() -> None:
    engine = ConfigEngine(Required, app_name="parsecraft")
    with pytest.raises(ConfigValidationError, match="host"):
        engine.load()


def test_error_attributes() -> None:
    assert issubclass(ConfigValidationError, ConfigError)
    secret_error = ConfigSecretError("TOKEN")
    assert secret_error.variable == "TOKEN"
    assert "TOKEN" in str(secret_error)
    assert ConfigFileError("x.toml").cause is None


def test_report_defaults() -> None:
    report = ConfigCheckReport(valid=True)
    assert report.errors == ()
    assert report.warnings == ()
    assert report.snapshot is None
    assert ConfigShowEntry(key="k", value=1).source is None
