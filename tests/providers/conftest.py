"""Fixtures valid only in the providers test group."""

from __future__ import annotations

import pytest

from tests.fixtures.jev_sdk import StubSdk


@pytest.fixture
def jev_sdk(monkeypatch: pytest.MonkeyPatch) -> StubSdk:
    """A fake ``typesafe_sdk`` module installed in ``sys.modules``.

    The System One providers import their SDK through ``importlib`` at
    ``load_judge`` time, so installing this stub exercises the real provider
    code offline (see ``tests/fixtures/jev_sdk.py``).
    """
    return StubSdk().install(monkeypatch)
