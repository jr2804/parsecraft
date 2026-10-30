"""``parsecraft benchmark`` — reproducible reports over local documents."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from parsecraft.backends import default_registry
from parsecraft.backends.registry import BackendRegistry
from parsecraft.benchmark import (
    BenchmarkReport,
    run_benchmark,
    to_json,
    to_markdown,
    write_json,
    write_markdown,
)
from parsecraft.cli.convert import _effective_timeout_s
from parsecraft.environment import constraints_from_environment, probe_environment


def benchmark_report(
    paths: Sequence[Path],
    registry: BackendRegistry = default_registry,
    *,
    max_passes: int = 1,
    allow_ocr: bool | None = None,
    timeout_s: float | None = None,
) -> BenchmarkReport:
    """Benchmark every eligible backend over local documents (offline).

    ``formats`` stays empty: the harness filters candidates by each document's
    own media type, so a single format restriction would be wrong for a mix.
    The effective deadline is the flag when given, else the ``timeout_s``
    config key — the same ``_effective_timeout_s`` rule as ``convert``: the
    harness bounds the same ``backend.convert`` calls, so a second semantic
    would be surprising.
    """
    constraints = constraints_from_environment(probe_environment(), formats=(), allow_ocr=allow_ocr, max_passes=max_passes)
    return run_benchmark(list(paths), registry, constraints, timeout_s=_effective_timeout_s(timeout_s))


def write_reports(report: BenchmarkReport, output_dir: Path) -> list[Path]:
    """Write both deterministic report files into ``output_dir``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    return [write_json(report, output_dir / "benchmark.json"), write_markdown(report, output_dir / "benchmark.md")]


def render(report: BenchmarkReport, *, as_json: bool) -> str:
    """Render the report as JSON or Markdown."""
    return to_json(report) if as_json else to_markdown(report)
