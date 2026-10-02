"""CPU-only smoke tests: CLI runs, package metadata wires up, module entry works."""

from __future__ import annotations

import contextlib
import importlib
import importlib.metadata
import io
import json
import logging
import runpy
import sys
from importlib.metadata import PackageNotFoundError

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
from parsecraft.cli import commands, output
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
        capabilities=BackendCapabilities(supported_formats=["txt", "md"], gpu_requirement=0.0),
    )

    def __call__(self, config: object) -> DocumentBackend:
        return _StubBackend()


class _GpuFactory:
    descriptor = BackendDescriptor(
        name="gpu-fake",
        capabilities=BackendCapabilities(
            supported_formats=["pdf"],
            gpu_requirement=1.0,
            estimated_vram_gb=4.5,
        ),
    )

    def __call__(self, config: object) -> DocumentBackend:
        return _StubBackend()


class _OptionalGpuFactory:
    descriptor = BackendDescriptor(
        name="either-fake",
        capabilities=BackendCapabilities(
            supported_formats=["pdf"],
            gpu_requirement=0.5,
            estimated_vram_gb=2.0,
        ),
    )

    def __call__(self, config: object) -> DocumentBackend:
        return _StubBackend()


class _NoFormatsFactory:
    descriptor = BackendDescriptor(name="bare-fake", capabilities=BackendCapabilities())

    def __call__(self, config: object) -> DocumentBackend:
        return _StubBackend()


def test_commands_share_the_process_default_registry() -> None:
    assert commands.default_registry is default_registry


def test_cp1252_stdout_rejects_a_non_codepage_glyph() -> None:
    """The control: this is the failure class the CLI must not inherit."""
    stream, _ = _cp1252_stream()
    with pytest.raises(UnicodeEncodeError, match="charmap"):
        stream.write("\u25aa")


def test_ensure_utf8_streams_switches_a_codepage_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout, stdout_bytes = _cp1252_stream()
    stderr, _ = _cp1252_stream()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    output.ensure_utf8_streams()

    assert output._is_utf8(stdout.encoding)
    assert output._is_utf8(stderr.encoding)
    assert stdout.write("\u25aa") == 1  # no UnicodeEncodeError
    stdout.flush()
    assert stdout_bytes.getvalue() == "\u25aa".encode()  # the glyph, preserved as UTF-8


# ── Output encoding (pc-edn) ────────────────────────────────────────────────


def _cp1252_stream() -> tuple[io.TextIOWrapper, io.BytesIO]:
    """A stdout the way Windows gives it to a redirected CLI: the legacy codepage.

    Returns the wrapper plus the raw buffer behind it, so a test can read the
    bytes the platform would have received.
    """
    raw = io.BytesIO()
    return io.TextIOWrapper(raw, encoding="cp1252", errors="strict", newline=""), raw


def test_ensure_utf8_streams_leaves_a_utf8_stream_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="surrogateescape", newline="")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    output.ensure_utf8_streams()
    assert stream.errors == "surrogateescape"  # never rewrite a stream we do not need to


def test_ensure_utf8_streams_ignores_streams_without_reconfigure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    output.ensure_utf8_streams()  # a replaced stream is not ours to fix


def test_ensure_utf8_streams_tolerates_a_refusing_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    class Refusing:
        encoding = "cp1252"

        @staticmethod
        def reconfigure(**_kwargs: object) -> None:
            msg = "I/O operation on closed file"
            raise ValueError(msg)

    monkeypatch.setattr(sys, "stdout", Refusing())
    monkeypatch.setattr(sys, "stderr", Refusing())
    output.ensure_utf8_streams()  # detached/closed: keep the default, never raise


def test_ensure_utf8_streams_accepts_encoding_spellings() -> None:
    assert output._is_utf8("utf-8")
    assert output._is_utf8("UTF8")
    assert output._is_utf8("utf_8")
    assert not output._is_utf8("cp1252")
    assert not output._is_utf8(None)


def test_asset_info_channel_is_configured_once_per_invocation() -> None:
    """The root callback installs the parsecraft.assets → stderr channel, idempotently."""
    channel = logging.getLogger("parsecraft.assets")
    _runner.invoke(app, ["--version"])
    _runner.invoke(app, ["--version"])
    handlers = [handler for handler in channel.handlers if isinstance(handler, output._CurrentStderrHandler)]
    assert len(handlers) == 1  # installed once, no matter how often the CLI runs
    assert channel.level == logging.INFO
    assert channel.propagate is False  # no root handler can duplicate the line


def test_asset_info_channel_emits_to_the_current_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    """A first-use notice lands on stderr — never on stdout, where the IR lives."""
    output.ensure_asset_info_logging()
    logging.getLogger("parsecraft.assets").info("downloading acme/model (1.0 GiB) into /cache/acme--model/abc123")
    captured = capsys.readouterr()
    assert "downloading acme/model (1.0 GiB) into /cache/acme--model/abc123" in captured.err
    assert captured.out == ""


def test_version_short_flag_matches_long_flag() -> None:
    short = _runner.invoke(app, ["-v"])
    long = _runner.invoke(app, ["--version"])
    assert short.exit_code == 0
    assert short.output == long.output


def test_backends_empty_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands, "default_registry", _registry_with())
    result = _runner.invoke(app, ["backends"])
    assert result.exit_code == 0
    assert "No backends registered." in result.output


def test_backends_json_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands, "default_registry", _registry_with())
    result = _runner.invoke(app, ["backends", "--json"])
    assert result.exit_code == 0
    assert result.output.strip() == "[]"


def test_backends_table_covers_cpu_and_gpu_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands, "default_registry", _registry_with(_CpuFactory(), _GpuFactory(), _OptionalGpuFactory()))
    result = _runner.invoke(app, ["backends"])
    assert result.exit_code == 0
    lines = result.output.splitlines()
    assert len(lines) == 3
    cpu_line, either_line, gpu_line = lines  # registry order is by name
    assert "cpu-fake" in cpu_line
    assert "txt,md" in cpu_line
    assert "gpu" not in cpu_line
    assert "gpu-fake" in gpu_line
    assert "vram<=4.5G" in gpu_line
    assert "pdf" in gpu_line
    # The middle of the scale reads as "either", never as a hard GPU row:
    assert "either-fake" in either_line
    assert "cpu/gpu" in either_line
    assert "vram<=2G" in either_line


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
    assert set(entry) == {"name", "version", "capabilities"}
    assert set(entry["capabilities"]) == {
        "supported_formats",
        "supports_page_ranges",
        "supports_multi_page",
        "gpu_requirement",
        "estimated_vram_gb",
        "optional_dependency_group",
        "model_asset",
        "languages",
    }


def test_backends_json_enabled_via_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands, "default_registry", _registry_with(_CpuFactory()))
    result = _runner.invoke(app, ["backends"], env={"PARSECRAFT_JSON": "1"})
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert [entry["name"] for entry in payload] == ["cpu-fake"]


def _registry_with(*factories: BackendFactory) -> BackendRegistry:
    registry = BackendRegistry()
    registry._entry_points_loaded = True  # isolate from installed entry points
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
    def _missing(name: str) -> str:
        msg = name
        raise PackageNotFoundError(msg)

    monkeypatch.setattr("parsecraft.cli.app.version", _missing)
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
