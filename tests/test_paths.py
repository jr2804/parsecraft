"""The directory policy: home-relative roots, one table, no platform dirs.

The policy exists because the OS application-data directory (``%LOCALAPPDATA%``
on Windows) buries the files several levels deep in the user profile. Every
consumer resolves its directory through ``parsecraft.paths``; nothing else
may pick a location of its own.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from parsecraft import paths

_SRC = Path(__file__).resolve().parent.parent / "src" / "parsecraft"

_PLATFORMDIRS_IMPORT = re.compile(r"^\s*(?:import platformdirs|from platformdirs import)", re.MULTILINE)


def test_cache_root_is_home_relative() -> None:
    assert paths.cache_root() == Path.home() / ".cache" / "parsecraft"
    assert paths.models_cache_dir() == paths.cache_root() / "models"
    assert paths.conversions_cache_dir() == paths.cache_root() / "conversions"


def test_config_root_is_home_relative() -> None:
    assert paths.config_root() == Path.home() / ".config" / "parsecraft"
    assert paths.global_config_file() == paths.config_root() / "config.toml"


def test_temp_dir_is_created_under_the_cache_root() -> None:
    temp = paths.temp_dir()
    assert temp == paths.cache_root() / "tmp"
    assert temp.is_dir()


def test_only_the_generic_config_engine_may_use_platformdirs() -> None:
    """No package picks its own directory: two named exceptions, no others.

    The config engine is a self-contained, vendorable package, so it keeps the
    platform-appropriate default. ``paths.py`` names the pre-2026.11 location
    once, as a migration probe, so a store written there is still found. Every
    parsecraft package resolves through ``parsecraft.paths`` instead.
    """
    offenders = sorted(
        str(path.relative_to(_SRC))
        for path in _SRC.rglob("*.py")
        if _PLATFORMDIRS_IMPORT.search(path.read_text(encoding="utf-8")) and path.name not in {"engine.py", "paths.py"}
    )
    assert offenders == []


def test_legacy_models_cache_dir_is_none_when_the_location_did_not_move(monkeypatch: pytest.MonkeyPatch) -> None:
    """On a platform where the old path is the current one there is nothing to look for."""
    monkeypatch.setattr(paths, "user_cache_path", lambda app: paths.cache_root())
    assert paths.legacy_models_cache_dir() is None


def test_legacy_models_cache_dir_names_the_old_location(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Where the location did move (Windows, macOS) the old one is named."""
    monkeypatch.setattr(paths, "user_cache_path", lambda app: tmp_path / "app")
    assert paths.legacy_models_cache_dir() == tmp_path / "app" / "models"
