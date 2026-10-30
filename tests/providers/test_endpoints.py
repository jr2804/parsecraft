"""Tests for judge endpoint resolution (config file, generic env, CLI override)."""

from __future__ import annotations

from pathlib import Path

import pytest

from parsecraft.providers._endpoints import (
    JUDGE_API_KEY_ENV,
    JUDGE_BASE_URL_ENV,
    JUDGE_MODEL_ENV,
    override_endpoint,
    resolve_endpoint,
    select_judge_spec,
    select_text_classifier_spec,
)
from parsecraft.routing.judge_providers import JudgeProviderUnavailableError


@pytest.fixture(autouse=True)
def _clean_judge_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Generic judge env vars start unset; each test opts in explicitly."""
    monkeypatch.delenv(JUDGE_MODEL_ENV, raising=False)
    monkeypatch.delenv(JUDGE_BASE_URL_ENV, raising=False)
    monkeypatch.delenv(JUDGE_API_KEY_ENV, raising=False)


def test_select_judge_spec_prefers_the_cli_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(JUDGE_MODEL_ENV, "zen/from-env")
    assert select_judge_spec("ollama/from-cli") == "ollama/from-cli"


def test_select_judge_spec_prefers_env_over_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_global(tmp_path, '[judge]\nmodel = "ollama/from-file"\n')
    monkeypatch.setenv(JUDGE_MODEL_ENV, "zen/from-env")
    assert select_judge_spec(None) == "zen/from-env"


def test_select_judge_spec_reads_the_file_model(tmp_path: Path) -> None:
    _write_global(tmp_path, '[judge]\nmodel = "zen/from-file"\n')
    assert select_judge_spec(None) == "zen/from-file"


def test_select_judge_spec_is_none_when_nothing_configures_a_model() -> None:
    assert select_judge_spec(None) is None


def test_select_judge_spec_ignores_a_blank_file_model(tmp_path: Path) -> None:
    _write_global(tmp_path, '[judge]\nmodel = "   "\n')
    assert select_judge_spec(None) is None


def test_select_text_classifier_spec_reads_the_section(tmp_path: Path) -> None:
    _write_global(tmp_path, '[text_classifier]\nmodel = "stub/from-file"\n')
    assert select_text_classifier_spec() == "stub/from-file"


def test_select_text_classifier_spec_ignores_the_routing_judge_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """pc-9p6.5: neither ``[judge]`` nor ``PARSECRAFT_JUDGE_MODEL`` activates classification."""
    _write_global(tmp_path, '[judge]\nmodel = "typesafe-ai/jev-latest"\n')
    monkeypatch.setenv(JUDGE_MODEL_ENV, "typesafe-ai/jev-latest")
    assert select_text_classifier_spec() is None


def test_select_text_classifier_spec_is_none_when_unconfigured() -> None:
    assert select_text_classifier_spec() is None


def test_resolve_endpoint_defaults_to_the_profile_when_unconfigured() -> None:
    assert resolve_endpoint("zen", default_base_url="https://default.invalid", legacy_api_key_env="ZEN_KEY") == (
        "https://default.invalid",
        None,
    )


def test_resolve_endpoint_reads_the_exact_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_global(tmp_path, '[providers.zen]\nbase_url = "https://zen.invalid"\napi_key = "${ZEN_KEY}"\n')
    monkeypatch.setenv("ZEN_KEY", "file-key")
    assert resolve_endpoint("zen", default_base_url="https://default.invalid", legacy_api_key_env="ZEN_KEY") == (
        "https://zen.invalid",
        "file-key",
    )


def test_resolve_endpoint_maps_the_typesafe_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_global(tmp_path, '[providers.typesafe]\napi_key = "${TS_KEY}"\n')
    monkeypatch.setenv("TS_KEY", "alias-key")
    _, api_key = resolve_endpoint("typesafe-ai", default_base_url=None, legacy_api_key_env="TYPESAFE_API_KEY")
    assert api_key == "alias-key"


def test_resolve_endpoint_maps_the_ollama_cloud_alias(tmp_path: Path) -> None:
    _write_global(tmp_path, '[providers.ollama-cloud]\nbase_url = "https://cloud.invalid"\n')
    base_url, api_key = resolve_endpoint("ollama", default_base_url="http://localhost:11434", legacy_api_key_env=None)
    assert base_url == "https://cloud.invalid"
    assert api_key is None


def test_resolve_endpoint_prefers_the_exact_token_over_the_alias(tmp_path: Path) -> None:
    _write_global(tmp_path, '[providers.typesafe-ai]\nbase_url = "https://exact.invalid"\n[providers.typesafe]\nbase_url = "https://alias.invalid"\n')
    base_url, _ = resolve_endpoint("typesafe-ai", default_base_url=None, legacy_api_key_env=None)
    assert base_url == "https://exact.invalid"


def test_resolve_endpoint_prefers_generic_env_over_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_global(tmp_path, '[providers.zen]\nbase_url = "https://file.invalid"\napi_key = "${ZEN_KEY}"\n')
    monkeypatch.setenv("ZEN_KEY", "file-key")
    monkeypatch.setenv(JUDGE_BASE_URL_ENV, "https://env.invalid")
    monkeypatch.setenv(JUDGE_API_KEY_ENV, "env-key")
    assert resolve_endpoint("zen", default_base_url="https://default.invalid", legacy_api_key_env="ZEN_KEY") == (
        "https://env.invalid",
        "env-key",
    )


def test_resolve_endpoint_prefers_the_cli_override_over_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_global(tmp_path, '[providers.zen]\nbase_url = "https://file.invalid"\napi_key = "${ZEN_KEY}"\n')
    monkeypatch.setenv("ZEN_KEY", "file-key")
    monkeypatch.setenv(JUDGE_BASE_URL_ENV, "https://env.invalid")
    monkeypatch.setenv(JUDGE_API_KEY_ENV, "env-key")
    with override_endpoint("https://cli.invalid", "cli-key"):
        assert resolve_endpoint("zen", default_base_url="https://default.invalid", legacy_api_key_env="ZEN_KEY") == (
            "https://cli.invalid",
            "cli-key",
        )


def test_resolve_endpoint_ignores_blank_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_global(tmp_path, '[providers.zen]\nbase_url = "   "\n')
    monkeypatch.setenv(JUDGE_BASE_URL_ENV, "   ")
    base_url, _ = resolve_endpoint("zen", default_base_url="https://default.invalid", legacy_api_key_env=None)
    assert base_url == "https://default.invalid"


def test_resolve_endpoint_falls_back_to_the_legacy_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZEN_KEY", "legacy-key")
    assert resolve_endpoint("zen", default_base_url=None, legacy_api_key_env="ZEN_KEY") == (None, "legacy-key")


def test_resolve_endpoint_names_the_provider_on_an_unexpandable_secret(tmp_path: Path) -> None:
    _write_global(tmp_path, '[providers.zen]\napi_key = "${ZEN_MISSING}"\n')
    with pytest.raises(JudgeProviderUnavailableError, match="zen"):
        resolve_endpoint("zen", default_base_url=None, legacy_api_key_env="ZEN_KEY")


def test_select_judge_spec_names_the_seam_on_an_unexpandable_secret(tmp_path: Path) -> None:
    _write_global(tmp_path, '[judge]\nmodel = "${JUDGE_MISSING}"\n')
    with pytest.raises(JudgeProviderUnavailableError, match="judge"):
        select_judge_spec(None)


def test_resolve_endpoint_names_the_provider_on_a_malformed_file(tmp_path: Path) -> None:
    _write_global(tmp_path, "providers = [\n")
    with pytest.raises(JudgeProviderUnavailableError, match="zen"):
        resolve_endpoint("zen", default_base_url=None, legacy_api_key_env=None)


def test_select_text_classifier_spec_names_the_section_on_a_malformed_file(tmp_path: Path) -> None:
    _write_global(tmp_path, "text_classifier = [\n")
    with pytest.raises(JudgeProviderUnavailableError, match="text_classifier"):
        select_text_classifier_spec()


def _write_global(tmp_path: Path, text: str) -> None:
    path = tmp_path / "global" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_override_endpoint_resets_after_the_block() -> None:
    with override_endpoint("https://cli.invalid", "cli-key"):
        pass
    base_url, api_key = resolve_endpoint("zen", default_base_url="https://default.invalid", legacy_api_key_env=None)
    assert (base_url, api_key) == ("https://default.invalid", None)


def test_override_endpoint_resets_on_error() -> None:
    with pytest.raises(RuntimeError, match="boom"), override_endpoint("https://cli.invalid", "cli-key"):
        raise RuntimeError("boom")
    base_url, _ = resolve_endpoint("zen", default_base_url="https://default.invalid", legacy_api_key_env=None)
    assert base_url == "https://default.invalid"


def test_override_endpoint_carries_unset_knobs_as_none() -> None:
    with override_endpoint(None, None):
        base_url, api_key = resolve_endpoint("zen", default_base_url="https://default.invalid", legacy_api_key_env=None)
        assert (base_url, api_key) == ("https://default.invalid", None)
