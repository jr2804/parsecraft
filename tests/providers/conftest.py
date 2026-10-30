"""Fixtures valid only in the providers test group."""

from __future__ import annotations

from pathlib import Path

import pytest

from parsecraft.cli import config as cli_config
from tests.fixtures.jev_sdk import StubSdk


@pytest.fixture(autouse=True)
def _isolated_global_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Provider loaders read the file endpoint config — never a real one."""
    monkeypatch.setattr(cli_config, "global_config_path", lambda: tmp_path / "global" / "config.toml")


@pytest.fixture
def jev_sdk(monkeypatch: pytest.MonkeyPatch) -> StubSdk:
    """A fake ``typesafe_sdk`` module installed in ``sys.modules``.

    The System One providers import their SDK through ``importlib`` at
    ``load_judge`` time, so installing this stub exercises the real provider
    code offline (see ``tests/fixtures/jev_sdk.py``).
    """
    return StubSdk().install(monkeypatch)
