r"""Deterministic JSON/Markdown writers for benchmark reports.

Stable key ordering (``sort_keys``), stable row ordering (results sorted by
(document, backend)), fixed float rounding, LF newlines everywhere —
reports diff cleanly between commits. No wall-clock values are written.
"""

from __future__ import annotations

import json
from pathlib import Path

from parsecraft.benchmark.models import BenchmarkMetrics, BenchmarkReport

_HEADING = "# ParseCraft benchmark report"
_RESULTS_HEADING = "## Results"
_SKIPS_HEADING = "## Skips"
_DASH = "—"


def write_json(report: BenchmarkReport, path: Path) -> Path:
    """Write the JSON representation with LF newlines; return the path."""
    path.write_text(to_json(report), encoding="utf-8", newline="\n")
    return path


def to_json(report: BenchmarkReport) -> str:
    """Serialize the report as deterministic, sorted, indented JSON."""
    payload = report.model_dump(mode="json")
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def write_markdown(report: BenchmarkReport, path: Path) -> Path:
    """Write the Markdown representation with LF newlines; return the path."""
    path.write_text(to_markdown(report), encoding="utf-8", newline="\n")
    return path


def to_markdown(report: BenchmarkReport) -> str:
    """Render the report as a Markdown document with stable tables."""
    lines = [
        _HEADING,
        "",
        f"package: `{report.package_version}`",
        "",
        _RESULTS_HEADING,
        "",
        "| document | backend | selected | elapsed_s | pages | pages/s | chunks | coverage | peak bytes | failures |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines.extend(_result_row(row) for row in report.results)
    if report.skips:
        lines.extend(
            [
                "",
                _SKIPS_HEADING,
                "",
                "| document | reason |",
                "| --- | --- |",
            ]
        )
        lines.extend(f"| {skip.document} | {skip.reason} |" for skip in report.skips)
    return "\n".join(lines) + "\n"


def _result_row(row: BenchmarkMetrics) -> str:
    chunks = ",".join(f"{kind}={count}" for kind, count in sorted(row.chunk_counts.items()) if count)
    failure_codes = "; ".join(failure.code.value for failure in row.failures) or _DASH
    selected = "-" if row.selected is None else ("yes" if row.selected else "no")
    coverage = "-" if row.text_coverage is None else f"{row.text_coverage:.6f}"
    pages_per_second = "-" if row.pages_per_second is None else f"{row.pages_per_second:.6f}"
    return (
        f"| {row.document} | {row.backend} | {selected} | {row.elapsed_s:.6f} "
        f"| {row.pages} | {pages_per_second} | {chunks or '-'} | {coverage} "
        f"| {row.peak_memory_bytes} | {failure_codes} |"
    )
