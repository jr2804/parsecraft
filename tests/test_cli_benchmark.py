"""Tests for ``parsecraft benchmark`` (offline harness wrapper)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner, Result

import parsecraft.cli.benchmark as benchmark_module
from parsecraft.backends import BackendRegistry
from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    BackendRef,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
    SourceDocument,
)
from parsecraft.cli.app import app
from parsecraft.cli.errors import CliError
from parsecraft.environment import EnvironmentInfo
from parsecraft.ir import ChunkKind, PageResult, PageSignal, StructuredChunk

_TEXT = "A paragraph comfortably longer than the forty character routing threshold."

runner = CliRunner()


class _StubBackend:
    def __init__(self, name: str, capabilities: BackendCapabilities) -> None:
        self.name = name
        self.capabilities = capabilities

    def convert(self, request: ConversionRequest) -> BackendResult:
        start = request.page_range.start if request.page_range is not None else 1
        return BackendResult(
            backend=BackendRef(name=self.name, version="0.0.0"),
            pages=[
                PageResult(
                    page_number=start,
                    blocks=[
                        StructuredChunk(
                            id=f"{self.name}-{start}-0",
                            kind=ChunkKind.PARAGRAPH,
                            content="benchmark content",
                            page_number=start,
                            reading_order=0,
                        )
                    ],
                )
            ],
            elapsed_s=0.0,
        )

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        data = source.content or b""
        return AnalysisResult(
            source_hash=hashlib.sha256(data).hexdigest(),
            page_count=1,
            signals=[PageSignal(page_number=1, has_native_text=True, text_chars=len(data), image_count=0, blank=False)],
        )


class _StubFactory:
    def __init__(self, descriptor: BackendDescriptor) -> None:
        self.descriptor = descriptor

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        return _StubBackend(self.descriptor.name, self.descriptor.capabilities)


def test_benchmark_report_measures_eligible_backend(tmp_path: Path, offline_probe: None) -> None:
    report = benchmark_module.benchmark_report([_document(tmp_path)], _registry())
    assert [row.backend for row in report.results] == ["native-text"]
    assert report.skips == []


def test_benchmark_report_skips_missing_document(tmp_path: Path, offline_probe: None) -> None:
    report = benchmark_module.benchmark_report([tmp_path / "missing.txt"], _registry())
    assert report.results == []
    assert report.skips[0].reason == "file not found"


def test_write_reports_and_render(tmp_path: Path, offline_probe: None) -> None:
    report = benchmark_module.benchmark_report([_document(tmp_path)], _registry())
    written = benchmark_module.write_reports(report, tmp_path / "out")
    assert [path.name for path in written] == ["benchmark.json", "benchmark.md"]
    assert (tmp_path / "out" / "benchmark.json").is_file()
    assert "ParseCraft benchmark report" in benchmark_module.render(report, as_json=False)
    assert "results" in benchmark_module.render(report, as_json=True)


def test_cli_benchmark_markdown(tmp_path: Path, offline_probe: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _cli_registry(monkeypatch)
    result = runner.invoke(app, ["benchmark", str(_document(tmp_path))])
    assert result.exit_code == 0
    assert "# ParseCraft benchmark report" in result.output


def test_cli_benchmark_json_and_output(tmp_path: Path, offline_probe: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _cli_registry(monkeypatch)
    document = _document(tmp_path)
    result = runner.invoke(app, ["benchmark", str(document), "--json"])
    assert result.exit_code == 0
    assert "results" in json.loads(result.output)

    output = tmp_path / "reports"
    written = runner.invoke(app, ["benchmark", str(document), "--markdown", "--output", str(output)])
    assert written.exit_code == 0
    assert (output / "benchmark.json").is_file()
    assert "wrote" in _text(written)


def test_cli_benchmark_conflicting_flags(tmp_path: Path, offline_probe: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _cli_registry(monkeypatch)
    result = runner.invoke(app, ["benchmark", str(_document(tmp_path)), "--json", "--markdown"])
    assert result.exit_code == 2
    assert "mutually exclusive" in _text(result)


def test_cli_benchmark_no_ocr_and_missing_file(tmp_path: Path, offline_probe: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _cli_registry(monkeypatch)
    assert runner.invoke(app, ["benchmark", str(_document(tmp_path)), "--no-ocr"]).exit_code == 0
    assert runner.invoke(app, ["benchmark", str(tmp_path / "missing.txt")]).exit_code == 2


def test_cli_benchmark_reports_cli_error(tmp_path: Path, offline_probe: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise CliError("harness blew up")

    monkeypatch.setattr(benchmark_module, "benchmark_report", _boom)
    result = runner.invoke(app, ["benchmark", str(_document(tmp_path))])
    assert result.exit_code == 1
    assert "harness blew up" in _text(result)


def _text(result: Result) -> str:
    return f"{result.output}{result.stderr or ''}"


def test_cli_benchmark_json_payload(tmp_path: Path, offline_probe: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _cli_registry(monkeypatch)
    result = runner.invoke(app, ["benchmark", str(_document(tmp_path)), "--json"])
    payload = cast("dict[str, object]", json.loads(result.output))
    rows = cast("list[object]", payload["results"])
    assert cast("dict[str, object]", rows[0])["backend"] == "native-text"


@pytest.fixture
def offline_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(benchmark_module, "probe_environment", lambda: EnvironmentInfo(installed_extras=frozenset(), vram_budget_gb=0.0, offline=True))


def _document(tmp_path: Path, name: str = "doc.txt") -> Path:
    path = tmp_path / name
    path.write_text(_TEXT, encoding="utf-8")
    return path


def _cli_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(benchmark_module, "default_registry", _registry())


def _registry() -> BackendRegistry:
    registry = BackendRegistry()
    registry.register("native-text", _StubFactory(_descriptor()))
    return registry


def _descriptor(name: str = "native-text") -> BackendDescriptor:
    return BackendDescriptor(name=name, capabilities=BackendCapabilities(supported_formats=["text/plain"]))
