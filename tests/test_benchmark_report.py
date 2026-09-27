"""Guard the committed Phase 2 report artifacts (schema + writer round-trip)."""

from __future__ import annotations

import json
from pathlib import Path

from parsecraft.benchmark import BenchmarkMetrics, BenchmarkReport, to_json, to_markdown
from parsecraft.ir.models import ChunkKind

_ARTIFACTS = Path(__file__).parent.parent / "docs" / "benchmarks"
_JSON_PATH = _ARTIFACTS / "benchmark.json"
_MD_PATH = _ARTIFACTS / "benchmark.md"

_RESULT_FIELDS = {
    "analyzer_text_chars",
    "backend",
    "chunk_counts",
    "document",
    "elapsed_s",
    "emitted_chars",
    "failures",
    "pages",
    "pages_per_second",
    "peak_memory_bytes",
    "selected",
    "text_coverage",
}


def test_committed_json_matches_current_writer_byte_for_byte() -> None:
    # Round-trip guard: the committed artifact must be exactly what today's
    # schema + writer emit for the recorded data — schema drift breaks this.
    assert _JSON_PATH.read_text(encoding="utf-8", newline="") == to_json(_report())


def test_committed_markdown_matches_current_writer() -> None:
    assert _MD_PATH.read_text(encoding="utf-8", newline="") == to_markdown(_report())


def test_report_shape_is_pinned() -> None:
    report = _report()
    assert set(BenchmarkReport.model_fields) == {"package_version", "results", "skips"}
    assert report.package_version
    assert report.results, "the committed report must contain measured rows"
    assert report.skips, "unsupported documents must be recorded as honest skips"
    for row in report.results:
        assert set(BenchmarkMetrics.model_fields) == _RESULT_FIELDS
        assert set(row.chunk_counts) == {kind.value for kind in ChunkKind}
        assert row.elapsed_s >= 0
        assert row.peak_memory_bytes >= 0
        assert row.emitted_chars >= 0
        assert row.analyzer_text_chars >= 0
        assert row.text_coverage is None or row.text_coverage >= 0
        assert row.pages_per_second is None or row.pages_per_second >= 0


def test_rows_are_sorted_and_keys_are_deterministic() -> None:
    report = _report()
    rows = [(row.document, row.backend) for row in report.results]
    assert rows == sorted(rows)
    skips = [(skip.document, skip.reason) for skip in report.skips]
    assert skips == sorted(skips)
    payload = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
    assert list(payload) == sorted(payload)


def test_cpu_backends_and_licensed_pdfs_are_present() -> None:
    report = _report()
    documents = {row.document for row in report.results}
    backends = {row.backend for row in report.results}
    assert "itu-t-p863.pdf" in documents
    assert "etsi-ts-103558.pdf" in documents
    assert any("nist-sp-800-53r5" in document for document in documents)
    assert {"native-text", "native-markdown", "native-html", "native-pdf"} <= backends


def _report() -> BenchmarkReport:
    return BenchmarkReport.model_validate(json.loads(_JSON_PATH.read_text(encoding="utf-8")))
