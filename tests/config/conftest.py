"""Fixtures valid only in the config test group."""

from __future__ import annotations

from pathlib import Path

import pytest

_tests_dir = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def data_dir() -> Path:
    """Return the path to the test data directory."""
    return _tests_dir / "data"
