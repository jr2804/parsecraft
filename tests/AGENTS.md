# AGENTS.md — tests/

## Purpose

Pin the contracts declared in the source-tree `AGENTS.md` files with executable
tests, and keep the 100% coverage gate green.

## Ownership

- `conftest.py` — shared fixtures (`doc_factory`, `cache_subdir`,
  `downloads_dir`) plus the opt-in gates for network and corpus tests.
- `fixtures/documents.py` — deterministic synthetic document generators
  (.txt/.md/.csv/.json/.html + a minimal PDF built from stdlib bytes, plus an
  image-only-first-page scan PDF for mixed-OCR fixtures).
- `fixtures/sources.py` + `fixtures/sources.toml` — public test-document
  manifest: license, URL, why suitable, documented local download step, plus
  corpus expectations as data (`difficulty`, `pages`, `approx_size`,
  `features`, `sha256`).
- `test_ir.py`, `test_markdown_projection.py` — IR invariants and the
  deterministic Markdown projection (incl. nested children).
- `test_backends_registry.py`, `test_example_backend.py`, `test_backends_ocr.py`
  — backend protocol, registry precedence, third-party example, OCR/VLM
  adapters (heavy stacks stubbed through `sys.modules`, fully offline).
- `test_routing_classifier.py`, `test_providers_pdfinspector.py` — the OCR-need
  classifier seam and its pdf-inspector provider (`pdf_inspector` stubbed
  through `sys.modules`; the live 1-based indexing test skips without the
  extra).
- `test_smoke.py` — CLI surface (`backends --json`, `--version`, bare
  invocation shows help).
- `test_document_fixtures.py`, `test_sources_manifest.py` — fixture
  determinism/PDF validity, manifest schema, corpus tier (opt-in).
- `test_packaging.py`, `test_offline_import.py`, `test_template_validation.py`
  — wheel contents, offline import contract, template invariants.
- `typing_consumer.py` — type-level consumer contract (checked by `ty`).
- `test-cache/` — pytest-owned cache dir; scaffolded synthetic documents
  only; self-ignored, never committed. Never written to directly — access it
  through the `cache` fixture / `cache_subdir`.
- `downloads/` — gitignored staging for real corpus documents, written only by
  the corpus tier (via `downloads_dir`) or by hand following a manifest
  `download_step`. Deliberately outside `test-cache/`.

## Local Contracts

- No committed document binaries: fixtures are generator code only.
  Synthetic documents are scaffolded into the pytest cache
  (`tests/test-cache/`, via the `cache` fixture); real corpus documents are
  staged in `tests/downloads/` (ADR-0001 §8).
- Markers and tiers:
  - `network` — skipped unless `pytest --run-downloads` (or `--run-corpus`).
  - `corpus` — slow tier, skipped unless `pytest --run-corpus`; runs via
    `mise run test-corpus`; hard budget **15 min on a cold cache**
    (pinned by `test_corpus_cold_cache_refresh_stays_within_budget`).
  - `gpu` — skipped unless `pytest --run-gpu`; runs inside the isolated
    `.venv-gpu` (torch/transformers live only there, never in `.venv`);
    heavy deps load via `importlib.import_module` inside test bodies so the
    default run never touches them.
  - Corpus downloads live under `tests/downloads/`. Pinned sources cache
    content-addressed (`sha256-filename`) and fail loudly on drift; re-pin
    deliberately after review. Live endpoints are marked `mutable = true`:
    cached by filename and validated structurally (never by hash), so upstream
    edits do not fail the tier.
- `mise test` always stays offline and fast — only the default tier runs.
- Never weaken `fail_under = 100` (`pyproject.toml`) or the offline import
  gate (`test_offline_import.py`).

## Verification

`mise test` (100% coverage) · `mise run all` (test + lint + spell + format +
format-md + docs) · `mise run test-corpus` (network tier, ≤ 15 min cold).

## Child DOX Index

None — leaf boundary.
