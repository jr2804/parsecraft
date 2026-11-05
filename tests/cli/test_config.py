"""Tests for the ``parsecraft config`` CLI group."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner, Result

from parsecraft.cli import config
from parsecraft.cli.app import app
from parsecraft.config import ConfigCheckReport, ConfigError, ConfigShowEntry, ConfigValidationError, ConfigWarning
from parsecraft.routing.models import RoutingConstraints, RoutingPreference

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_global_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the global config path inside ``tmp_path`` so tests never read a real one."""
    monkeypatch.setattr(config, "global_config_path", lambda: tmp_path / "global" / "config.toml")


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


def test_global_config_path_ignores_platformdirs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The app global config lives at ``~/.config/parsecraft/`` on every platform (pc-66v)."""
    monkeypatch.undo()  # drop the autouse isolation: exercise the real seam
    assert config.global_config_path() == Path.home() / ".config" / "parsecraft" / "config.toml"


def test_config_check_accepts_providers_and_judge_sections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEN_KEY", "file-key")
    path = _project_file(
        tmp_path,
        '[providers.zen]\nbase_url = "https://zen.invalid"\napi_key = "${ZEN_KEY}"\n[judge]\nmodel = "zen/jev-1.13"\n',
    )
    result = runner.invoke(app, ["config", "check", "--config-file", str(path)])
    assert result.exit_code == 0
    assert "Configuration is valid." in _text(result)


def test_config_check_rejects_unknown_provider_keys(tmp_path: Path) -> None:
    path = _project_file(tmp_path, '[providers.zen]\napi_key_env = "ZEN_KEY"\n')
    result = runner.invoke(app, ["config", "check", "--config-file", str(path)])
    assert result.exit_code == 1
    assert "providers.zen" in _text(result)


def test_config_check_rejects_unknown_judge_keys(tmp_path: Path) -> None:
    path = _project_file(tmp_path, '[judge]\nmodels = "zen/jev-1.13"\n')
    result = runner.invoke(app, ["config", "check", "--config-file", str(path)])
    assert result.exit_code == 1
    assert "judge" in _text(result)


def test_config_check_accepts_text_classifier_section(tmp_path: Path) -> None:
    """pc-9p6.5: the dedicated activation surface validates like [judge]."""
    path = _project_file(tmp_path, '[text_classifier]\nmodel = "typesafe-ai/jev-preview"\n')
    result = runner.invoke(app, ["config", "check", "--config-file", str(path)])
    assert result.exit_code == 0
    assert "Configuration is valid." in _text(result)


def test_config_check_rejects_unknown_text_classifier_keys(tmp_path: Path) -> None:
    path = _project_file(tmp_path, '[text_classifier]\nmodels = "typesafe-ai/jev-preview"\n')
    result = runner.invoke(app, ["config", "check", "--config-file", str(path)])
    assert result.exit_code == 1
    assert "text_classifier" in _text(result)


def test_config_show_redacts_provider_api_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _project_file(tmp_path, '[providers.typesafe]\napi_key = "${TS_KEY}"\n')
    monkeypatch.setenv("TS_KEY", "s3cr3t")
    result = runner.invoke(app, ["config", "show", "--config-file", str(path), "--json"])
    assert result.exit_code == 0
    assert _entry(result.output, "providers.typesafe.api_key")["value"] == "***"


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


def test_routing_settings_defaults() -> None:
    settings = config.ParsecraftConfig().routing
    assert settings.preference == RoutingPreference.BALANCED
    assert settings.allow_ocr is None
    assert settings.engines == []
    assert settings.preferred_backend is None
    assert settings.only_backend is None
    assert settings.backend_options == {}
    assert settings.language is None
    assert settings.max_passes == 1
    assert settings.vram_budget_gb == 0.0
    assert settings.gpu_usable is False


def test_routing_constraints_maps_the_section() -> None:
    settings = config.RoutingSettings(
        preference=RoutingPreference.QUALITY,
        allow_ocr=False,
        engines=["pandoc", "tesseract"],
        preferred_backend="pdf-inspector",
        only_backend="",
        backend_options={"pdf-inspector": {"mode": "fast", "dpi": 200}},
        language="de",
        max_passes=3,
        vram_budget_gb=2.5,
        gpu_usable=True,
    )
    constraints = settings.routing_constraints(installed_extras={"pdf-inspector"}, formats={"application/pdf"})
    assert constraints.preference == RoutingPreference.QUALITY
    assert constraints.allow_ocr is False
    assert constraints.engines == frozenset({"pandoc", "tesseract"})
    assert constraints.preferred_backend == "pdf-inspector"
    assert constraints.only_backend is None  # empty string normalizes to None
    assert constraints.backend_options == {"pdf-inspector": {"mode": "fast", "dpi": 200}}
    assert constraints.language == "de"
    assert constraints.max_passes == 3
    assert constraints.vram_budget_gb == 2.5
    assert constraints.gpu_usable is True
    assert constraints.installed_extras == {"pdf-inspector"}
    assert constraints.formats == {"application/pdf"}
    assert constraints.offline is True


def test_routing_constraints_keeps_allow_ocr_derivation_and_offline_override() -> None:
    derived = config.RoutingSettings().routing_constraints(installed_extras=set(), formats=set())
    assert derived.allow_ocr is True  # None means "keep the planner's derivation"
    overridden = config.RoutingSettings().routing_constraints(installed_extras=set(), formats=set(), offline=False)
    assert overridden.offline is False


def test_routing_constraints_rejects_unknown_backend(tmp_path: Path) -> None:
    settings = config.RoutingSettings(preferred_backend="no-such-backend")
    with pytest.raises(ConfigError, match="routing.preferred_backend") as excinfo:
        settings.routing_constraints(installed_extras=set(), formats=set())
    assert "accepted:" in str(excinfo.value)


def test_routing_constraints_rejects_unknown_engine(tmp_path: Path) -> None:
    settings = config.RoutingSettings(engines=["no-such-engine"])
    with pytest.raises(ConfigError, match="routing.engines"):
        settings.routing_constraints(installed_extras=set(), formats=set())


def test_routing_constraints_wraps_validation_failures() -> None:
    """Schema drift between the two models still surfaces as ConfigValidationError."""
    settings = config.RoutingSettings.model_construct(max_passes=0)  # bypass the schema bounds
    with pytest.raises(ConfigValidationError, match="failed validation"):
        settings.routing_constraints(installed_extras=set(), formats=set())


def test_config_check_accepts_routing_section(tmp_path: Path) -> None:
    path = _project_file(
        tmp_path,
        '[routing]\npreference = "quality"\nengines = ["pandoc"]\npreferred_backend = "pdf-inspector"\n'
        '[routing.backend_options.pdf-inspector]\nmode = "fast"\nmax_passes = 2\n',
    )
    result = runner.invoke(app, ["config", "check", "--config-file", str(path)])
    assert result.exit_code == 0
    assert "Configuration is valid." in _text(result)


def test_config_check_rejects_unknown_routing_backend(tmp_path: Path) -> None:
    path = _project_file(tmp_path, '[routing]\npreferred_backend = "no-such-backend"\n')
    result = runner.invoke(app, ["config", "check", "--config-file", str(path)])
    assert result.exit_code == 1
    assert "routing.preferred_backend" in _text(result)
    assert "no-such-backend" in _text(result)


def test_config_check_rejects_unknown_routing_engine(tmp_path: Path) -> None:
    path = _project_file(tmp_path, '[routing]\nengines = ["marker"]\n')
    result = runner.invoke(app, ["config", "check", "--config-file", str(path)])
    assert result.exit_code == 1
    assert "routing.engines" in _text(result)
    assert "marker" in _text(result)


def test_config_check_rejects_unknown_routing_keys(tmp_path: Path) -> None:
    path = _project_file(tmp_path, "[routing]\ntypo_key = 1\n")
    result = runner.invoke(app, ["config", "check", "--config-file", str(path)])
    assert result.exit_code == 1
    assert "typo_key" in _text(result)


def test_config_check_rejects_invalid_routing_values(tmp_path: Path) -> None:
    path = _project_file(tmp_path, "[routing]\nmax_passes = 0\n")
    result = runner.invoke(app, ["config", "check", "--config-file", str(path)])
    assert result.exit_code == 1
    assert "max_passes" in _text(result)


def test_config_check_rejects_unknown_only_backend(tmp_path: Path) -> None:
    path = _project_file(tmp_path, '[routing]\nonly_backend = "no-such-backend"\n')
    result = runner.invoke(app, ["config", "check", "--config-file", str(path)])
    assert result.exit_code == 1
    assert "routing.only_backend" in _text(result)


def _text(result: Result) -> str:
    return f"{result.output}{result.stderr or ''}"


def test_build_engine_multi_file_priority_and_provenance(tmp_path: Path) -> None:
    vault = _project_file(_ensure_dir(tmp_path / "vault"), 'offline = true\n[routing]\npreference = "quality"\n')
    project = _project_file(_ensure_dir(tmp_path / "cwd"), "[routing]\nmax_passes = 4\n")
    engine = config.build_engine([vault, project])
    loaded = engine.load()
    assert loaded.routing.preference == RoutingPreference.QUALITY
    assert loaded.routing.max_passes == 4
    assert loaded.offline is True
    preference_source = engine.source_of("routing.preference")
    assert preference_source is not None
    assert preference_source.origin == str(vault)
    passes_source = engine.source_of("routing.max_passes")
    assert passes_source is not None
    assert passes_source.origin == str(project)
    offline_source = engine.source_of("offline")
    assert offline_source is not None
    assert offline_source.origin == str(vault)


def test_build_engine_later_file_wins_per_key(tmp_path: Path) -> None:
    vault = _project_file(_ensure_dir(tmp_path / "vault"), "offline = true\n")
    project = _project_file(_ensure_dir(tmp_path / "cwd"), "offline = false\n")
    engine = config.build_engine([vault, project])
    assert engine.load().offline is False
    offline_source = engine.source_of("offline")
    assert offline_source is not None
    assert offline_source.origin == str(project)


def test_config_driven_routing_journey_end_to_end(tmp_path: Path) -> None:
    """The documented consumer recipe, whole: two files, real names, one constraints.

    Vault file sets the preference and a backend option, working-directory file
    overrides the pass budget and forbids OCR; `build_engine` merges them per
    key, and `routing_constraints()` turns the merged section into a valid
    `RoutingConstraints` without the caller re-deriving anything.
    """
    vault = _project_file(
        _ensure_dir(tmp_path / "vault"),
        '[routing]\npreference = "quality"\npreferred_backend = "pdf-inspector"\n[routing.backend_options.pdf-inspector]\nmode = "fast"\n',
    )
    project = _project_file(_ensure_dir(tmp_path / "cwd"), "[routing]\nmax_passes = 2\nallow_ocr = false\n")
    engine = config.build_engine([vault, project])
    constraints = engine.load().routing.routing_constraints(
        installed_extras={"pdf-inspector"},
        formats={"application/pdf"},
    )
    assert constraints.preference == RoutingPreference.QUALITY
    assert constraints.preferred_backend == "pdf-inspector"
    assert constraints.allow_ocr is False
    assert constraints.max_passes == 2
    assert constraints.backend_options == {"pdf-inspector": {"mode": "fast"}}
    assert constraints.offline is True  # RoutingConstraints' own safe default
    # The mapping's output is a valid RoutingConstraints verbatim: no re-derivation needed.
    assert RoutingConstraints.model_validate(constraints.model_dump()) == constraints
    preference_source = engine.source_of("routing.preferred_backend")
    assert preference_source is not None
    assert preference_source.origin == str(vault)
    passes_source = engine.source_of("routing.max_passes")
    assert passes_source is not None
    assert passes_source.origin == str(project)


def test_build_engine_empty_sequence_skips_project_layer(tmp_path: Path) -> None:
    _project_file(tmp_path, "offline = true\n")  # cwd project file exists but must be ignored
    engine = config.build_engine([])
    assert engine.load().offline is False  # schema default survives
    offline_source = engine.source_of("offline")
    assert offline_source is not None
    assert offline_source.origin == "<defaults>"


def test_config_scope_installs_the_runs_settings(tmp_path: Path) -> None:
    """A scoped run reads exactly the settings it installed (pc-8e6)."""
    vault = _project_file(_ensure_dir(tmp_path / "vault"), "timeout_s = 42.0\n")
    settings = config.build_engine([vault]).load()
    with config.config_scope(settings):
        assert config.effective_settings() is settings
        assert config.effective_settings().timeout_s == 42.0


def test_config_scope_without_settings_keeps_the_ambient_one(tmp_path: Path) -> None:
    """A nested scope that carries nothing cannot widen or narrow the outer one."""
    vault = _project_file(_ensure_dir(tmp_path / "vault"), "timeout_s = 42.0\n")
    settings = config.build_engine([vault]).load()
    with config.config_scope(settings), config.config_scope(None):
        assert config.effective_settings() is settings


def test_config_scope_resets_after_the_block(tmp_path: Path) -> None:
    """Past the scope the default layer set is read again."""
    vault = _project_file(_ensure_dir(tmp_path / "vault"), "timeout_s = 42.0\n")
    settings = config.build_engine([vault]).load()
    with config.config_scope(settings):
        assert config.effective_settings() is settings
    assert config.effective_settings() is not settings
    assert config.effective_settings().timeout_s is None


def test_config_scope_resets_when_the_body_raises(tmp_path: Path) -> None:
    """A failed run leaves no scope behind."""
    vault = _project_file(_ensure_dir(tmp_path / "vault"), "timeout_s = 42.0\n")
    settings = config.build_engine([vault]).load()
    with pytest.raises(RuntimeError, match="boom"), config.config_scope(settings):
        raise RuntimeError("boom")
    assert config.effective_settings() is not settings


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_effective_settings_reads_the_default_layer_set_without_a_scope(tmp_path: Path) -> None:
    """No scope: the global file, the working directory's project file, and the environment."""
    global_path = tmp_path / "global" / "config.toml"
    global_path.parent.mkdir(parents=True, exist_ok=True)
    global_path.write_text("timeout_s = 11.0\n", encoding="utf-8")
    assert config.effective_settings().timeout_s == 11.0


def _project_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "parsecraft.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_example_config_matches_the_schema() -> None:
    """The root example must load cleanly and document every schema key.

    Sync rule owned by src/parsecraft/config/AGENTS.md: any ParsecraftConfig
    or engine change checks/updates parsecraft.toml.example. This test pins
    key coverage (new fields fail here); comment/semantic drift stays human.
    """
    example = Path(__file__).resolve().parents[2] / "parsecraft.toml.example"
    raw = tomllib.loads(example.read_text(encoding="utf-8"))
    settings = config.ParsecraftConfig.model_validate(raw)
    assert set(raw) == set(config.ParsecraftConfig.model_fields)
    assert set(raw["routing"]) == set(config.RoutingSettings.model_fields)
    assert settings.timeout_s == 90.0
    assert settings.routing.backend_options["docling"] == {"do_ocr": False}
    assert settings.providers["typesafe"].api_key == "${TYPESAFE_API_KEY}"
    assert settings.judge.model == "typesafe-ai/jev-1.13"
