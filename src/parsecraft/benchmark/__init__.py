"""Reproducible backend benchmarks over local documents (Phase 2 harness).

``run_benchmark(documents, registry, constraints, judge=None)`` measures
every eligible backend per document; ``to_json``/``to_markdown`` render
deterministic, diff-clean reports. Offline: local paths only, never fetch.
See ``AGENTS.md`` for the full contract.
"""

from __future__ import annotations

from parsecraft.benchmark.models import (
    BenchmarkFailure,
    BenchmarkMetrics,
    BenchmarkReport,
    BenchmarkSkip,
)
from parsecraft.benchmark.runner import run_benchmark
from parsecraft.benchmark.writers import to_json, to_markdown, write_json, write_markdown

__all__ = [
    "BenchmarkFailure",
    "BenchmarkMetrics",
    "BenchmarkReport",
    "BenchmarkSkip",
    "run_benchmark",
    "to_json",
    "to_markdown",
    "write_json",
    "write_markdown",
]
