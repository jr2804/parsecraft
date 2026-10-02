"""Third-party output policy: quiet by default, untouched with ``--verbose``.

Offline and side-effect free: only environment variables and logger levels are
inspected, and every case restores what it touched.
"""

from __future__ import annotations

import logging
import os

import pytest

from parsecraft.cli import verbosity


@pytest.fixture(autouse=True)
def _clear_quiet_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from an environment without our quiet defaults, on any host."""
    for name in verbosity.QUIET_ENV:
        monkeypatch.delenv(name, raising=False)


def test_default_quiet_sets_the_library_environment() -> None:
    with verbosity.third_party_output(verbose=False):
        for name, value in verbosity.QUIET_ENV.items():
            assert os.environ[name] == value


def test_default_quiet_holds_third_party_loggers_at_error() -> None:
    noisy = logging.getLogger("transformers")
    previous = noisy.level
    with verbosity.third_party_output(verbose=False):
        assert logging.getLogger("transformers").level == logging.ERROR
        assert logging.getLogger("huggingface_hub").level == logging.ERROR
    assert logging.getLogger("transformers").level == previous


def test_env_and_levels_are_restored_after_the_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRANSFORMERS_VERBOSITY", "warning")
    with verbosity.third_party_output(verbose=False):
        assert os.environ["TRANSFORMERS_VERBOSITY"] == "error"
    assert os.environ["TRANSFORMERS_VERBOSITY"] == "warning"


def test_a_variable_that_was_absent_is_removed_again() -> None:
    """Restoring means deleting what we introduced, not writing an empty value."""
    with verbosity.third_party_output(verbose=False):
        assert "HF_HUB_DISABLE_PROGRESS_BARS" in os.environ
    assert "HF_HUB_DISABLE_PROGRESS_BARS" not in os.environ


def test_verbose_suppresses_nothing() -> None:
    """``--verbose`` quiets nothing — a pre-set value and level both survive."""
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "0"
    noisy = logging.getLogger("transformers")
    noisy.setLevel(logging.DEBUG)
    try:
        with verbosity.third_party_output(verbose=True):
            assert os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] == "0"
            assert logging.getLogger("transformers").level == logging.DEBUG
    finally:
        noisy.setLevel(logging.NOTSET)
        os.environ.pop("HF_HUB_DISABLE_PROGRESS_BARS", None)
