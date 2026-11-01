"""Guard the committed Phase 2 report artifacts (schema + writer round-trip)."""

from __future__ import annotations

import json
from pathlib import Path

from parsecraft.benchmark import BenchmarkMetrics, BenchmarkReport, to_json, to_markdown
from parsecraft.ir.models import ChunkKind

_ARTIFACTS = Path(__file__).resolve().parents[2] / "docs" / "benchmarks"
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


# ── GPU OCR artifacts (delivered passes, same schema) ───────────────────────

_OCR_PASS_A = _ARTIFACTS / "benchmark-ocr-pass-a.json"
_OCR_PASS_B = _ARTIFACTS / "benchmark-ocr-pass-b.json"

# ── GPU OCR five-way (single window, all five models on the shipped 300 dpi) ─

_GPU_FIVEWAY = _ARTIFACTS / "benchmark-gpu-fiveway-2026-10-31.json"
_GPU_FIVEWAY_MD = _ARTIFACTS / "benchmark-gpu-fiveway-2026-10-31.md"
_FIVEWAY_OCR_BACKENDS = {"ocr-ovis", "ocr-qianfan", "ocr-surya", "ocr-tele", "ocr-unlimited"}


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
    assert any(document.endswith("itu-t-p863.pdf") for document in documents)
    assert any(document.endswith("etsi-ts-103558.pdf") for document in documents)
    assert any("nist-sp-800-53r5" in document for document in documents)
    assert {"native-text", "native-markdown", "native-html", "pymupdf"} <= backends


def test_refresh_pins_csv_measurement_and_json_skip() -> None:
    # Guards the 2026-10-31 refresh: the CSV row must be a real conversion
    # (never a reverted "unsupported" artifact) and the JSON skip recorded.
    # A factual test of these committed artifacts, not a general requirement.
    report = _report()
    csv_rows = [row for row in report.results if row.backend == "native-csv" and row.document.endswith("exoplanets.csv")]
    assert csv_rows, "the committed report must measure exoplanets.csv via native-csv"
    for row in csv_rows:
        assert row.failures == []
        assert row.chunk_counts.get("table", 0) >= 1
    assert any(skip.document.endswith("wikidata-q42.json") for skip in report.skips)


def _report() -> BenchmarkReport:
    return BenchmarkReport.model_validate(json.loads(_JSON_PATH.read_text(encoding="utf-8")))


def test_gpu_fiveway_artifacts_match_the_current_writer_byte_for_byte() -> None:
    # The five-way ran on one commit in one environment; both artifacts are
    # writer output for that one report, so a schema change must fail here
    # rather than leaving a page quoting numbers whose shape we no longer emit.
    report = BenchmarkReport.model_validate_json(_GPU_FIVEWAY.read_text(encoding="utf-8"))
    assert _GPU_FIVEWAY.read_text(encoding="utf-8", newline="") == to_json(report)
    assert _GPU_FIVEWAY_MD.read_text(encoding="utf-8", newline="") == to_markdown(report)


def test_gpu_fiveway_covers_all_five_models_and_records_every_failure() -> None:
    # Guards the 2026-10-31 five-way: one window for all five OCR models, three
    # fixtures, and typed failures recorded for the models that could not run at
    # the shipped raster. A factual test of this committed artifact.
    report = BenchmarkReport.model_validate_json(_GPU_FIVEWAY.read_text(encoding="utf-8"))
    rows = [(row.document, row.backend) for row in report.results]
    assert rows == sorted(rows)
    assert len(report.results) == 24, "three fixtures x eight backends"
    assert report.skips == [], "every document and backend ran; nothing was skipped"
    assert "9cbb645" in report.package_version
    backends = {row.backend for row in report.results}
    assert backends >= _FIVEWAY_OCR_BACKENDS
    assert {"liteparse", "pdf-inspector", "pymupdf"} <= backends
    won: dict[str, int] = dict.fromkeys(_FIVEWAY_OCR_BACKENDS, 0)
    for row in report.results:
        # "completed" = both fixture pages emitted with no typed failure. A model
        # that returns one page and then errors did not complete the fixture.
        if row.backend in won and row.pages == 2 and not row.failures:
            won[row.backend] += 1
    # qianfan was the only model that completed every fixture at 300 dpi; the
    # other four are expected to fail here and MUST be recorded, not dropped.
    assert won["ocr-qianfan"] == 3
    for name in _FIVEWAY_OCR_BACKENDS - {"ocr-qianfan"}:
        assert won[name] < 3, f"{name} unexpectedly completed all three fixtures — revisit the page"
    failed = [row for row in report.results if row.backend in _FIVEWAY_OCR_BACKENDS and row.failures]
    assert failed, "the OOM cells must carry their typed failures"


def test_ocr_artifacts_validate_sort_and_cover_all_models() -> None:
    reports = [BenchmarkReport.model_validate_json(path.read_text(encoding="utf-8")) for path in (_OCR_PASS_A, _OCR_PASS_B)]
    backends: set[str] = set()
    converted = 0
    failed = 0
    for report in reports:
        rows = [(row.document, row.backend) for row in report.results]
        assert rows == sorted(rows)
        assert report.results
        for row in report.results:
            backends.add(row.backend)
            converted += row.pages
            failed += len(row.failures)
    # all four OCR models appear across the two transformer-split passes
    assert {"ocr-ovis", "ocr-qianfan", "ocr-tele", "ocr-unlimited"} <= backends
    # real conversions happened (2-page fixtures) and typed failures were
    # recorded for the wrong-major pair — never silently dropped
    assert converted > 0
    assert failed > 0
