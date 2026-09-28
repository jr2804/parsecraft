"""Tests for ``parsecraft inspect`` (analysis signals + routing preview)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner, Result

import parsecraft.cli.convert as convert_module
from parsecraft.backends import BackendRegistry
from parsecraft.backends.errors import BackendError, DependencyUnavailableError
from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
    SourceDocument,
)
from parsecraft.cli import commands as commands_module
from parsecraft.cli import inspect as inspect_module
from parsecraft.cli.app import app
from parsecraft.environment import EnvironmentInfo
from parsecraft.ir import Diagnostic, DiagnosticLevel, PageSignal

_LONG_TEXT = "A paragraph comfortably longer than the forty character routing threshold."

runner = CliRunner()


class _StubBackend:
    def __init__(
        self,
        name: str,
        capabilities: BackendCapabilities,
        *,
        text_chars: int,
        blank: bool,
        ratio: float | None,
        diagnostics: tuple[Diagnostic, ...],
        emit_signals: bool,
    ) -> None:
        self.name = name
        self.capabilities = capabilities
        self._text_chars = text_chars
        self._blank = blank
        self._ratio = ratio
        self._diagnostics = diagnostics
        self._emit_signals = emit_signals

    def analyze(self, source: SourceDocument) -> AnalysisResult:
        data = source.content or b""
        signals = (
            [
                PageSignal(
                    page_number=1,
                    has_native_text=not self._blank,
                    text_chars=self._text_chars,
                    image_count=0,
                    blank=self._blank,
                    replacement_char_ratio=self._ratio,
                )
            ]
            if self._emit_signals
            else []
        )
        return AnalysisResult(
            source_hash=hashlib.sha256(data).hexdigest(),
            page_count=1,
            signals=signals,
            diagnostics=list(self._diagnostics),
        )

    def convert(self, request: ConversionRequest) -> BackendResult:
        raise NotImplementedError


class _StubFactory:
    def __init__(self, descriptor: BackendDescriptor, **options: object) -> None:
        self.descriptor = descriptor
        self._options = options

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        return cast(
            "DocumentBackend",
            _StubBackend(
                self.descriptor.name,
                self.descriptor.capabilities,
                text_chars=cast("int", self._options.get("text_chars", 120)),
                blank=cast("bool", self._options.get("blank", False)),
                ratio=cast("float | None", self._options.get("ratio")),
                diagnostics=cast("tuple[Diagnostic, ...]", self._options.get("diagnostics", ())),
                emit_signals=cast("bool", self._options.get("emit_signals", True)),
            ),
        )


def test_inspect_source_previews_route(tmp_path: Path, offline_probe: None) -> None:
    preview = inspect_module.inspect_source(_source_file(tmp_path), _registry())
    assert preview.media_type == "text/plain"
    assert preview.routing_error is None
    assert preview.plan is not None
    assert preview.plan.primary == "native-text"


def test_inspect_source_without_signals(tmp_path: Path, offline_probe: None) -> None:
    preview = inspect_module.inspect_source(_source_file(tmp_path), _registry(emit_signals=False))
    assert preview.plan is None
    assert preview.routing_error == "analysis produced no page signals"


def test_inspect_source_reports_routing_error(tmp_path: Path, offline_probe: None) -> None:
    registry = BackendRegistry()
    registry.register("native-text", _StubFactory(_descriptor(group="missing-extra")))
    preview = inspect_module.inspect_source(_source_file(tmp_path), registry)
    assert preview.plan is None
    assert preview.routing_error is not None
    assert "no eligible backend" in preview.routing_error


def test_render_preview_plan_lines(tmp_path: Path, offline_probe: None) -> None:
    diagnostic = Diagnostic(level=DiagnosticLevel.INFO, code="demo", message="noticed")
    preview = inspect_module.inspect_source(_source_file(tmp_path), _registry(ratio=0.01, diagnostics=(diagnostic,)))
    lines = inspect_module.render_preview(preview)
    joined = "\n".join(lines)
    assert "source: text/plain" in joined
    assert "replacement=0.010" in joined
    assert "diagnostic [info] demo: noticed" in joined
    assert "routing: primary=native-text" in joined
    assert "reason:" in joined


def test_render_preview_error_line(tmp_path: Path, offline_probe: None) -> None:
    preview = inspect_module.inspect_source(_source_file(tmp_path), _registry(emit_signals=False))
    assert "routing: unavailable" in "\n".join(inspect_module.render_preview(preview))


def test_preview_payload_is_json_ready(tmp_path: Path, offline_probe: None) -> None:
    payload = inspect_module.preview_payload(inspect_module.inspect_source(_source_file(tmp_path), _registry()))
    assert payload["media_type"] == "text/plain"
    assert cast("dict[str, object]", payload["plan"])["primary"] == "native-text"


def test_cli_inspect_text(tmp_path: Path, offline_probe: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands_module, "default_registry", _registry())
    result = runner.invoke(app, ["inspect", str(_source_file(tmp_path))])
    assert result.exit_code == 0
    assert "routing: primary=native-text" in result.output


def test_cli_inspect_json(tmp_path: Path, offline_probe: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands_module, "default_registry", _registry())
    result = runner.invoke(app, ["inspect", str(_source_file(tmp_path)), "--json"])
    assert result.exit_code == 0
    payload = cast("dict[str, object]", json.loads(result.output))
    assert cast("dict[str, object]", payload["analysis"])["page_count"] == 1
    assert cast("dict[str, object]", payload["plan"])["primary"] == "native-text"


def test_cli_inspect_no_ocr(tmp_path: Path, offline_probe: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands_module, "default_registry", _registry())
    result = runner.invoke(app, ["inspect", str(_source_file(tmp_path)), "--no-ocr"])
    assert result.exit_code == 0


def test_cli_inspect_unsupported_suffix(tmp_path: Path, offline_probe: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands_module, "default_registry", _registry())
    path = tmp_path / "doc.xyz"
    path.write_text("data", encoding="utf-8")
    result = runner.invoke(app, ["inspect", str(path)])
    assert result.exit_code == 2
    assert "unsupported source" in _text(result)


def _registry(**options: object) -> BackendRegistry:
    registry = BackendRegistry()
    registry.register(_descriptor().name, _StubFactory(_descriptor(), **options))
    return registry


def test_cli_inspect_analysis_failure_maps_to_cli_error(tmp_path: Path, offline_probe: None, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = BackendRegistry()

    class FailingFactory:
        descriptor = _descriptor()

        @staticmethod
        def __call__(config: BackendConfig) -> DocumentBackend:
            raise BackendError("analysis exploded")

    registry.register("native-text", FailingFactory())
    monkeypatch.setattr(commands_module, "default_registry", registry)
    result = runner.invoke(app, ["inspect", str(_source_file(tmp_path))])
    assert result.exit_code == 1
    assert "analysis with 'native-text' failed" in _text(result)


def test_inspect_prefers_installed_claimer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An installed claimer beats a name-order-earlier backend whose extra is missing (pc-4u7.27)."""
    registry = BackendRegistry()
    monkeypatch.setattr("parsecraft.backends.registry.entry_points", lambda **kwargs: [])  # isolate: no real backends
    missing = Diagnostic(level=DiagnosticLevel.INFO, code="from-missing", message="extra not installed")
    installed = Diagnostic(level=DiagnosticLevel.INFO, code="from-installed", message="installed claimer")
    registry.register("aaa-liteparse", _StubFactory(_descriptor("aaa-liteparse", group="liteparse"), diagnostics=(missing,)))
    registry.register("zzz-ocr", _StubFactory(_descriptor("zzz-ocr", group="ocr-ovis"), diagnostics=(installed,)))
    monkeypatch.setattr(
        convert_module,
        "probe_environment",
        lambda: EnvironmentInfo(installed_extras=frozenset({"ocr-ovis"}), vram_budget_gb=8.0, offline=False),
    )
    preview = inspect_module.inspect_source(_source_file(tmp_path), registry)
    assert [diagnostic.code for diagnostic in preview.analysis.diagnostics] == ["from-installed"]


def test_cli_inspect_missing_dependency_maps_to_cli_error(tmp_path: Path, offline_probe: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """DependencyUnavailableError keeps exit 1 with its own actionable message (pc-4u7.28)."""
    registry = BackendRegistry()

    class MissingExtraFactory:
        descriptor = _descriptor()

        @staticmethod
        def __call__(config: BackendConfig) -> DocumentBackend:
            raise DependencyUnavailableError("some.module", "some-extra")

    registry.register("native-text", MissingExtraFactory())
    monkeypatch.setattr(commands_module, "default_registry", registry)
    result = runner.invoke(app, ["inspect", str(_source_file(tmp_path))])
    assert result.exit_code == 1
    assert "optional dependency missing" in _text(result)
    assert "backend dependency 'some.module' is not installed" in _text(result)


def _source_file(tmp_path: Path, name: str = "doc.txt", text: str = _LONG_TEXT) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_cli_inspect_unclaimed_media_type_is_usage_error(tmp_path: Path, offline_probe: None, monkeypatch: pytest.MonkeyPatch) -> None:
    # Supported suffix, but nothing here claims image/png → NoAnalyzerError
    # must map to exit 2 with the verbatim message.
    monkeypatch.setattr("parsecraft.backends.registry.entry_points", lambda **kwargs: [])
    registry = BackendRegistry()
    registry.register(_descriptor().name, _StubFactory(_descriptor()))
    monkeypatch.setattr(commands_module, "default_registry", registry)
    path = tmp_path / "scan.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    result = runner.invoke(app, ["inspect", str(path)])
    assert result.exit_code == 2
    assert "no installed backend can analyze image/png" in _text(result)


def _descriptor(name: str = "native-text", *, group: str | None = None) -> BackendDescriptor:
    return BackendDescriptor(
        name=name,
        capabilities=BackendCapabilities(supported_formats=["text/plain"], optional_dependency_group=group),
    )


@pytest.fixture
def offline_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(convert_module, "probe_environment", lambda: EnvironmentInfo(installed_extras=frozenset(), vram_budget_gb=0.0, offline=True))


def _text(result: Result) -> str:
    return f"{result.output}{result.stderr or ''}"
