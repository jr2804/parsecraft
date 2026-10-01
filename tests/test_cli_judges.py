"""``parsecraft judges`` — the provider/model catalog and the docs-table contract.

Fully offline: the command resolves no provider, reads no credential beyond its
presence, and never contacts an endpoint. One test parses the docs table and
compares it with the declared profiles, so a renamed or added spec cannot ship
with stale documentation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from parsecraft.cli import judges as judges_module
from parsecraft.cli.app import app
from parsecraft.providers import BUILTIN_JUDGE_PROVIDERS

_DOCS = Path(__file__).resolve().parents[1] / "docs" / "reference" / "cli.md"

_runner = CliRunner()


def test_human_output_lists_every_declared_spec(monkeypatch: pytest.MonkeyPatch, stash: None) -> None:
    result = _runner.invoke(app, ["judges"])
    assert result.exit_code == 0
    assert judges_module.DEFAULT_LINE in result.output
    for entry in _entries(monkeypatch):
        for spec in entry.specs:
            assert spec in result.output
    assert "verified local models: nimble, tev" in result.output
    assert "no credential" in result.output  # the local endpoint needs none


def test_human_output_reports_availability(monkeypatch: pytest.MonkeyPatch, stash: None) -> None:
    result = _runner.invoke(app, ["judges"])
    assert result.exit_code == 0
    assert "TYPESAFE_API_KEY (unset)" in result.output
    assert "extra systemone (missing)" in result.output


def test_human_output_reports_a_present_credential_and_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = _entries(monkeypatch, credential="TYPESAFE_API_KEY", extra=True)
    assert all(entry.extra_installed for entry in entries)
    lines = judges_module.render_entries(entries)
    assert "TYPESAFE_API_KEY (set)" in "\n".join(lines)
    assert "extra systemone (installed)" in "\n".join(lines)


def _entries(monkeypatch: pytest.MonkeyPatch, *, credential: str | None = None, extra: bool = False) -> list[judges_module.JudgeEntry]:
    """Force a deterministic availability picture for every provider."""
    if credential is not None:
        monkeypatch.setenv(credential, "key")
    monkeypatch.setattr(judges_module, "extra_present", lambda group: extra)
    return judges_module.entries()


def test_json_payload_carries_the_profile_and_the_specs(monkeypatch: pytest.MonkeyPatch, stash: None) -> None:
    result = _runner.invoke(app, ["judges", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert [item["name"] for item in payload] == list(BUILTIN_JUDGE_PROVIDERS)
    typesafe = payload[0]
    assert typesafe["specs"] == ["typesafe-ai/jev-latest", "typesafe-ai/jev-preview"]
    assert typesafe["api_key_env"] == "TYPESAFE_API_KEY"
    assert typesafe["extra"] == "systemone"
    assert typesafe["credential_set"] is False
    assert isinstance(typesafe["extra_installed"], bool)
    ollama = payload[-1]
    assert ollama["specs"] == ["ollama/<model>"]
    assert ollama["model_placeholder"] == "<model>"
    assert ollama["example_models"] == ["nimble", "tev"]
    assert ollama["credential_set"] is False  # no credential env declared


@pytest.fixture
def stash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin availability to "absent" so assertions never depend on this host."""
    monkeypatch.setattr(judges_module, "extra_present", lambda group: False)
    for profile in judges_module.entries():
        if profile.api_key_env is not None:
            monkeypatch.delenv(profile.api_key_env, raising=False)


def test_entries_cover_every_declared_provider() -> None:
    assert [entry.name for entry in judges_module.entries()] == list(BUILTIN_JUDGE_PROVIDERS)
    assert all(entry.extra == "systemone" for entry in judges_module.entries())


def test_the_docs_table_matches_the_declared_profiles() -> None:
    """Guard the spec spellings in ``docs/reference/cli.md`` against drift.

    Both directions matter: an undocumented new provider is invisible to readers,
    and a documented spec no provider accepts is a broken copy-paste example.
    """
    documented = set(re.findall(r"`([a-z0-9][a-z0-9_-]*/[^`]+)`", _docs_judge_table()))
    declared = {spec for entry in judges_module.entries() for spec in entry.specs}
    assert documented == declared
    assert documented, "the judge table parsed as empty — the docs layout changed"


def _docs_judge_table() -> str:
    """The ``#### Judge providers`` table body out of the CLI reference."""
    text = _DOCS.read_text(encoding="utf-8")
    section = text.split("#### Judge providers", 1)[1]
    return section.split("####", 1)[0]
