"""Tests for the shared backend-option coercion (one rule, every adapter)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import parsecraft.cli.convert as convert_module
from parsecraft.backends import BackendConfig, BackendDescriptor, BackendRegistry, DocumentBackend, coerce_option_value
from parsecraft.cli.app import app
from parsecraft.environment import EnvironmentInfo
from tests.cli.test_convert import _descriptor as cli_descriptor
from tests.cli.test_convert import _StubFactory

_LONG_TEXT = "first paragraph with enough characters to route natively.\n\nsecond paragraph, also long enough."

runner = CliRunner()


def test_boolean_tokens_become_real_booleans() -> None:
    """The spellings an operator actually types, in both cases and with padding."""
    for token in ("true", "TRUE", " True ", "yes", "on"):
        assert coerce_option_value(token) is True
    for token in ("false", "FALSE", " no ", "off"):
        assert coerce_option_value(token) is False


def test_numbers_become_numbers_and_anything_else_stays_a_string() -> None:
    """A non-obvious value stays recognisable so an adapter's error can quote it."""
    assert coerce_option_value("3") == 3
    assert coerce_option_value("1.5") == 1.5
    assert coerce_option_value("xhigh") == "xhigh"
    assert coerce_option_value("banana") == "banana"
    assert coerce_option_value("") == ""


def test_library_callers_values_pass_through_untouched() -> None:
    """A real bool/int from a library caller is never second-guessed."""
    assert coerce_option_value(True) is True
    assert coerce_option_value(0) == 0
    assert coerce_option_value(2.5) == 2.5


def test_the_cli_hands_adapters_real_types(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: `--backend-option x=true` reaches a validating adapter as a bool.

    Regression for the consumer report: the same flag behaved differently per
    adapter (`image_analysis=true` died in MinerU's boolean validator while
    `extract_vector_graphics=true` worked because LiteParse coerced internally).
    The factory here plays the strict adapter, so a regression fails the run.
    """

    registry = BackendRegistry()
    monkeypatch.setattr(convert_module, "default_registry", registry)
    monkeypatch.setattr(
        convert_module,
        "probe_environment",
        lambda: EnvironmentInfo(installed_extras=frozenset(), vram_budget_gb=0.0, offline=True),
    )
    descriptor = _descriptor("native-text", ("text/plain",))
    inner = _StubFactory(descriptor, content="validated content")

    class _StrictFactory:
        """An adapter that validates its option's type, like mineru does."""

        descriptor: BackendDescriptor

        def __call__(self, config: BackendConfig) -> DocumentBackend:
            # Only the dispatch pass carries the caller's options; the analyze pass
            # builds its own config (the planner's signals are what it needs).
            if "image_analysis" not in config.options:
                return inner(config)
            raw = config.options["image_analysis"]
            if not isinstance(raw, bool):
                msg = f"image_analysis must be a boolean, got {type(raw).__name__}"
                raise TypeError(msg)
            return inner(config)

    _StrictFactory.descriptor = descriptor  # the registry validates the attribute

    registry.register("native-text", _StrictFactory())
    source = tmp_path / "doc.txt"
    source.write_text(_LONG_TEXT, encoding="utf-8")

    result = runner.invoke(
        app,
        ["convert", str(source), "--backend", "native-text", "--backend-option", "image_analysis=true"],
    )
    assert result.exit_code == 0, f"{result.output}{result.stderr or ''}"


def _descriptor(name: str, formats: tuple[str, ...]) -> BackendDescriptor:

    return cli_descriptor(name, formats)
