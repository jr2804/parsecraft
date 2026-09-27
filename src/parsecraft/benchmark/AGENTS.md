# AGENTS.md — src/parsecraft/benchmark/

Reproducible backend benchmark harness (Phase 2).

## Purpose

`run_benchmark(documents, registry, constraints, judge=None) -> BenchmarkReport`
measures every eligible backend on every readable local document and renders
diff-clean reports via `to_json`/`to_markdown` (+ `write_json`/`write_markdown`).

## Ownership

- `runner.py` — `run_benchmark` and helpers (`_benchmark_document`,
  `_analyze`, `_selected_backend`, `_measure`, `_failure_from_exception`).
- `models.py` — `BenchmarkReport` / `BenchmarkMetrics` / `BenchmarkFailure` /
  `BenchmarkSkip`.
- `writers.py` — deterministic JSON/Markdown renderers.

## Local Contracts

- **Offline, local paths only.** The pinned corpus in `tests/downloads/`
  (Gutenberg/RFC/CommonMark/NIST + licensed P.863/ETSI PDFs) is READ, never
  fetched; missing/unreadable/unsupported files become `BenchmarkSkip`
  records — never an error, never a download.
- **No timestamps in reports** (they would break every diff); only stable
  values: sorted keys, results sorted by `(document, backend)`, skips sorted,
  floats rounded to 6 digits, LF newlines. Timing/memory are the measured
  data, not metadata.
- **Canonical analyzer rule** is reused from `parsecraft.cli.convert`
  (`MEDIA_TYPES` suffix map + `analysis_backend`: native first, then name
  order) — single source of truth with `convert --auto`; do not fork those
  heuristics here.
- **Eligibility** = `routing.rules.is_hard_eligible` + the document's media
  type; the judge only affects the `selected` flag (plan primary per
  document) — benchmarking itself forces no backend through the planner,
  because forcing would trip `plan_route._validate_order`'s intent-family
  rule by design.
- One backend instance at a time; instances dropped between analyze/convert
  (8 GB VRAM ceiling, same policy as `pipeline`).
- Peak memory = stdlib `tracemalloc` (Python-alloc proxy, no new deps).
- Failures stay typed: `BackendError` from create/convert →
  `dependency_missing` vs `backend_error` rows; reported `PassFailure`s are
  carried verbatim.

## Verification

`mise test` — `tests/test_benchmark.py` (100% coverage gate; tests use stub
backends and `tmp_path` documents — fully offline/CPU).
