"""CPU-only smoke tests: CLI runs, package metadata wires up, module entry works."""

from __future__ import annotations

import contextlib
import importlib
import importlib.metadata
import io
import json
import runpy
import sys

import pytest
from typer.testing import CliRunner

import parsecraft.__about__
from parsecraft.backends import (
    BackendCapabilities,
    BackendDescriptor,
    BackendFactory,
    BackendRegistry,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
    SourceDocument,
    default_registry,
)
from parsecraft.backends.errors import BackendLoadError
from parsecraft.backends.protocol import AnalysisResult, PageSignal
from parsecraft.cli import commands
from parsecraft.cli.app import app

_runner = CliRunner()


class _StubBackend:
    """Minimal DocumentBackend so factories satisfy the public Protocol."""

    name = "stub"
    capabilities = BackendCapabilities()

    def convert(self, request: ConversionRequest) -> BackendResult:
        return BackendResult(backend={"name": self.name, "version": "0"}, elapsed_s=0.0)

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        return AnalysisResult(
            source_hash="0" * 64,
            page_count=0,
            signals=[
                PageSignal(
                    page_number=1,
                    has_native_text=False,
                    text_chars=0,
                    image_count=0,
                    blank=True,
                )
            ],
        )


class _CpuFactory:
    descriptor = BackendDescriptor(
        name="cpu-fake",
        capabilities=BackendCapabilities(supported_formats=["txt", "md"], requires_gpu=False),
    )

    def __call__(self, config: object) -> DocumentBackend:
        return _StubBackend()


class _GpuFactory:
    descriptor = BackendDescriptor(
        name="gpu-fake",
        capabilities=BackendCapabilities(
            supported_formats=["pdf"],
            requires_gpu=True,
            estimated_vram_gb=4.5,
        ),
    )

    def __call__(self, config: object) -> DocumentBackend:
        return _StubBackend()


class _NoFormatsFactory:
    descriptor = BackendDescriptor(name="bare-fake", capabilities=BackendCapabilities())

    def __call__(self, config: object) -> DocumentBackend:
        return _StubBackend()


def test_bare_invocation_shows_help() -> None:
    result = _runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Usage:" in result.output
    assert "backends" in result.output


def test_commands_share_the_process_default_registry() -> None:
    assert commands.default_registry is default_registry


def test_version_short_flag_matches_long_flag() -> None:
    short = _runner.invoke(app, ["-v"])
    long = _runner.invoke(app, ["--version"])
    assert short.exit_code == 0
    assert short.output == long.output


def test_backends_empty_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands, "default_registry", BackendRegistry())
    result = _runner.invoke(app, ["backends"])
    assert result.exit_code == 0
    assert "No backends registered." in result.output


def test_backends_json_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands, "default_registry", BackendRegistry())
    result = _runner.invoke(app, ["backends", "--json"])
    assert result.exit_code == 0
    assert result.output.strip() == "[]"


def test_backends_table_covers_cpu_and_gpu_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands, "default_registry", _registry_with(_CpuFactory(), _GpuFactory()))
    result = _runner.invoke(app, ["backends"])
    assert result.exit_code == 0
    lines = result.output.splitlines()
    assert len(lines) == 2
    cpu_line, gpu_line = lines
    assert "cpu-fake" in cpu_line
    assert "txt,md" in cpu_line
    assert "gpu" not in cpu_line
    assert "gpu-fake" in gpu_line
    assert "vram<=4.5G" in gpu_line
    assert "pdf" in gpu_line


def test_backends_table_shows_placeholder_without_formats(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands, "default_registry", _registry_with(_NoFormatsFactory()))
    result = _runner.invoke(app, ["backends"])
    assert result.exit_code == 0
    assert "bare-fake" in result.output
    assert "-" in result.output


def test_backends_json_is_deterministic_and_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands, "default_registry", _registry_with(_GpuFactory(), _CpuFactory()))
    first = _runner.invoke(app, ["backends", "--json"])
    second = _runner.invoke(app, ["backends", "--json"])
    assert first.exit_code == 0
    assert first.output == second.output
    payload = json.loads(first.output)
    assert [entry["name"] for entry in payload] == ["cpu-fake", "gpu-fake"]
    assert payload[1]["capabilities"]["estimated_vram_gb"] == 4.5


def test_backends_json_capability_schema_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands, "default_registry", _registry_with(_CpuFactory(), _GpuFactory()))
    result = _runner.invoke(app, ["backends", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    entry = payload[0]
    assert set(entry) == {"name", "capabilities"}
    assert set(entry["capabilities"]) == {
        "supported_formats",
        "supports_page_ranges",
        "supports_multi_page",
        "requires_gpu",
        "estimated_vram_gb",
        "optional_dependency_group",
        "model_asset",
    }


def test_backends_json_enabled_via_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands, "default_registry", _registry_with(_CpuFactory()))
    result = _runner.invoke(app, ["backends"], env={"PARSECRAFT_JSON": "1"})
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert [entry["name"] for entry in payload] == ["cpu-fake"]


def _registry_with(*factories: BackendFactory) -> BackendRegistry:
    registry = BackendRegistry()
    for factory in factories:
        registry.register(factory.descriptor.name, factory)
    return registry


def test_backends_surfaces_load_errors_on_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = BackendRegistry()
    registry._load_errors["bad-plugin"] = BackendLoadError("bad-plugin", ImportError("nope"))
    monkeypatch.setattr(commands, "default_registry", registry)
    result = _runner.invoke(app, ["backends"])
    assert result.exit_code == 0
    combined = result.output + result.stderr_bytes.decode(encoding="utf-8", errors="replace")
    assert "warning: backend 'bad-plugin' failed to load" in combined


def test_version_flag_shows_metadata_version() -> None:
    result = _runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() != ""


def test_version_fallback_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(name: str) -> str:
        msg = "no dist"
        raise RuntimeError(msg)

    monkeypatch.setattr("parsecraft.cli.app.version", _boom)
    result = _runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == "0.0.0"


def test_bare_invocation_shows_help() -> None:
    result = _runner.invoke(app, [])
    assert result.exit_code == 2
    assert "Usage" in result.output


def test_module_entry_point_runs() -> None:
    argv_backup = sys.argv[:]
    sys.argv = ["parsecraft", "--version"]
    try:
        with contextlib.redirect_stdout(io.StringIO()) as stdout, pytest.raises(SystemExit) as excinfo:
            runpy.run_module("parsecraft", run_name="__main__")
    finally:
        sys.argv = argv_backup
    assert excinfo.value.code == 0
    assert stdout.getvalue().strip() != ""


def test_about_module_metadata_is_consistent() -> None:
    assert parsecraft.__about__.__title__ == "parsecraft"
    assert parsecraft.__about__.__license__ == "MIT"
    assert parsecraft.__about__.__version__


def test_version_fallback_when_distribution_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the PackageNotFoundError branch in parsecraft/__init__.py."""

    def _missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", _missing)
    importlib.reload(parsecraft)
    assert parsecraft.__version__ == "0.0.0"
    monkeypatch.undo()
    importlib.reload(parsecraft)  # restore the real distribution version
