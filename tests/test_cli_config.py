"""Tests for the ``parsecraft config`` CLI group."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import platformdirs
import pytest
from typer.testing import CliRunner, Result

from parsecraft.cli import config
from parsecraft.cli.app import app
from parsecraft.config import ConfigCheckReport, ConfigShowEntry, ConfigWarning

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_global_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the global config path inside ``tmp_path`` so tests never read a real one."""
    monkeypatch.setattr(platformdirs, "user_config_dir", lambda _app: str(tmp_path / "global"))


def test_config_help_lists_check_and_show() -> None:
    result = runner.invoke(app, ["config", "--help"])
    assert result.exit_code == 0
    assert "check" in result.output
    assert "show" in result.output


def test_config_without_subcommand_shows_help() -> None:
    result = runner.invoke(app, ["config"])
    assert "Usage" in result.output


def test_config_check_valid_text() -> None:
    result = runner.invoke(app, ["config", "check"])
    assert result.exit_code == 0
    assert "Configuration is valid." in result.output


def test_config_check_valid_json() -> None:
    result = runner.invoke(app, ["config", "check", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["valid"] is True
    assert payload["errors"] == []
    assert payload["snapshot"]["offline"] is False


def test_config_check_invalid_text(tmp_path: Path) -> None:
    path = _project_file(tmp_path, 'min_free_bytes = "abc"\n')
    result = runner.invoke(app, ["config", "check", "--config-file", str(path)])
    assert result.exit_code == 1
    assert "Configuration is invalid." in _text(result)
    assert "min_free_bytes" in _text(result)


def test_config_check_invalid_json(tmp_path: Path) -> None:
    path = _project_file(tmp_path, 'min_free_bytes = "abc"\n')
    result = runner.invoke(app, ["config", "check", "--config-file", str(path), "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["valid"] is False
    assert payload["errors"]


def test_config_check_malformed_file(tmp_path: Path) -> None:
    path = _project_file(tmp_path, "offline = \n")
    result = runner.invoke(app, ["config", "check", "--config-file", str(path)])
    assert result.exit_code == 1
    assert "cannot read config file" in _text(result)


def test_config_check_malformed_file_json(tmp_path: Path) -> None:
    path = _project_file(tmp_path, "offline = \n")
    result = runner.invoke(app, ["config", "check", "--config-file", str(path), "--json"])
    assert result.exit_code == 1
    assert "cannot read config file" in json.loads(result.output)["error"]


def test_config_show_defaults_text() -> None:
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "offline = false  (default:<defaults>)" in result.output


def test_config_show_json_has_provenance() -> None:
    result = runner.invoke(app, ["config", "show", "--json"])
    assert result.exit_code == 0
    offline = _entry(result.output, "offline")
    assert offline["value"] is False
    assert offline["source"] == {"layer": "default", "origin": "<defaults>"}


def test_config_show_reads_project_file_and_resolves_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _project_file(tmp_path, 'cache_dir = "assets"\noffline = true\n')
    result = runner.invoke(app, ["config", "show", "--json"])
    assert result.exit_code == 0
    cache_dir = _entry(result.output, "cache_dir")
    assert cache_dir["value"] == str(tmp_path / "assets")
    assert _source_layer(result.output, "cache_dir") == "project"


def test_config_show_reads_global_file(tmp_path: Path) -> None:
    global_dir = tmp_path / "global"
    global_dir.mkdir(parents=True)
    (global_dir / "config.toml").write_text("offline = true\n", encoding="utf-8")
    result = runner.invoke(app, ["config", "show", "--json"])
    assert result.exit_code == 0
    offline = _entry(result.output, "offline")
    assert offline["value"] is True
    assert _source_layer(result.output, "offline") == "global"


def test_config_file_option_that_does_not_exist_is_ignored(tmp_path: Path) -> None:
    result = runner.invoke(app, ["config", "show", "--config-file", str(tmp_path / "missing.toml"), "--json"])
    assert result.exit_code == 0
    assert _source_layer(result.output, "offline") == "default"


def test_config_show_redacts_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _project_file(tmp_path, 'hf_token = "${HF_TOKEN}"\n')
    monkeypatch.setenv("HF_TOKEN", "s3cr3t")
    result = runner.invoke(app, ["config", "show", "--config-file", str(path), "--json"])
    assert result.exit_code == 0
    assert _entry(result.output, "hf_token")["value"] == "***"
    text = runner.invoke(app, ["config", "show", "--config-file", str(path)])
    assert 'hf_token = "***"' in text.output


def test_config_show_missing_secret_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _project_file(tmp_path, 'hf_token = "${PARSECRAFT_TEST_UNSET}"\n')
    monkeypatch.delenv("PARSECRAFT_TEST_UNSET", raising=False)
    result = runner.invoke(app, ["config", "show", "--config-file", str(path)])
    assert result.exit_code == 1
    assert "PARSECRAFT_TEST_UNSET" in _text(result)

    as_json = runner.invoke(app, ["config", "show", "--config-file", str(path), "--json"])
    assert as_json.exit_code == 1
    assert "PARSECRAFT_TEST_UNSET" in json.loads(as_json.output)["error"]


def test_config_show_invalid_config_still_displays_raw_values(tmp_path: Path) -> None:
    path = _project_file(tmp_path, 'min_free_bytes = "abc"\n')
    result = runner.invoke(app, ["config", "show", "--config-file", str(path)])
    assert result.exit_code == 0
    assert 'min_free_bytes = "abc"' in result.output


def test_config_show_malformed_file(tmp_path: Path) -> None:
    path = _project_file(tmp_path, "offline = \n")
    result = runner.invoke(app, ["config", "show", "--config-file", str(path)])
    assert result.exit_code == 1
    assert "cannot read config file" in _text(result)


def _text(result: Result) -> str:
    return f"{result.output}{result.stderr or ''}"


def _project_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "parsecraft.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_config_env_layer_and_json_flag_no_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSECRAFT_CONFIG_OFFLINE", "true")
    monkeypatch.setenv("PARSECRAFT_JSON", "1")
    result = runner.invoke(app, ["config", "show", "--json"])
    assert result.exit_code == 0
    offline = _entry(result.output, "offline")
    assert offline["value"] is True
    assert _source_layer(result.output, "offline") == "env"


def _source_layer(payload: str, key: str) -> object:
    source = _entry(payload, key)["source"]
    return cast("dict[str, object]", source)["layer"]


def _entry(payload: str, key: str) -> dict[str, object]:
    return next(item for item in json.loads(payload) if item["key"] == key)


def test_check_lines_renders_status_warning_and_error() -> None:
    warning = ConfigWarning(code="deprecated_alias", message="key 'a' is deprecated", key="a")
    lines = config.check_lines(ConfigCheckReport(valid=True, warnings=(warning,)))
    assert lines[0] == (False, "Configuration is valid.")
    assert (True, "warning [deprecated_alias] key 'a' is deprecated") in lines

    invalid = config.check_lines(ConfigCheckReport(valid=False, errors=("boom",)))
    assert invalid[0] == (True, "Configuration is invalid.")
    assert (True, "error: boom") in invalid


def test_show_lines_marks_entries_without_source() -> None:
    assert config.show_lines([ConfigShowEntry(key="k", value=1)]) == ["k = 1  (unset)"]


def test_flatten_values_handles_nested_sections() -> None:
    assert config.flatten_values({"a": {"b": 1}, "c": 2}) == {"a.b": 1, "c": 2}
