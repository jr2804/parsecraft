# AGENTS.md — tests/

## Purpose

Pin the contracts declared in the source-tree `AGENTS.md` files with executable
tests, and keep the 100% coverage gate green.

## Layout

Test modules are grouped into submodules that mirror the `src/parsecraft/`
package layout; each contains only `test_<feature>.py` files:

`adapters/` · `assets/` · `backends/` · `benchmark/` · `cache/` · `cli/` ·
`config/` · `environment/` · `ir/` · `pipeline/` · `providers/` · `routing/`

Repo-level tests that pin project-wide invariants rather than one package stay
at the `tests/` root (`test_packaging.py`, `test_offline_import.py`,
`test_template_validation.py`, `test_smoke.py`, `test_example_backend.py`,
`test_conftest_offline_tripwire.py`) alongside the fixtures infrastructure
tests (`test_document_fixtures.py`, `test_sources_manifest.py`).

Every submodule is a package (`__init__.py`, like `tests/` itself), so feature
basenames may repeat across groups (`cli/test_config.py` vs
`config/test_config.py`) without pytest module-name collisions.

## Ownership

- `conftest.py` — the global machinery: the autouse offline tripwire
  (pc-mp2), the `pytest_addoption`/`pytest_configure`/
  `pytest_collection_modifyitems` tier gates, and the cross-group fixtures
  (`doc_factory`, `cache_subdir`, `subdir`, `downloads_dir`).
- `<group>/conftest.py` — exists **only** where a group owns fixtures no other
  group may see; today that is `providers/conftest.py` (`jev_sdk`) and
  `config/conftest.py` (`data_dir`, still pointing at `tests/data`). Do not
  add an empty conftest to a group without a scoped fixture.
- `fixtures/jev_sdk.py` — the fake `typesafe_sdk` the System One provider
  tests install in `sys.modules`; it records constructor kwargs, so endpoint
  injection (`base_url`, `api_key`, `timeout`) is asserted, not assumed.
- `fixtures/documents.py` — deterministic synthetic document generators
  (.txt/.md/.csv/.json/.html + a minimal PDF built from stdlib bytes, an
  image-only-first-page scan PDF for mixed-OCR fixtures, `image_heavy_pdf(n)`
  for the image-mass routing trigger, and `code_pdf()` — a positioned Courier
  listing for layout recovery). `docx_bytes()` and `pptx_bytes()` round it out
  for the office path: they are built by python-docx/python-pptx (dev-group
  test helpers) rather than by hand, because mupdf rejects hand-assembled
  OOXML — a complete fabricated package still fails to open — and ADR-0001 §8
  forbids committing the binary, so the generator stays the artifact.
  `xlsx_bytes()` does the same for the spreadsheet path, fetching openpyxl
  through `import_module` at call time rather than importing it, because
  openpyxl is an *extra* (not a dev dependency) and a module-level import would
  pull it into the canonical light env.
- `fixtures/sources.py` + `fixtures/sources.toml` — public test-document
  manifest: license, URL, why suitable, documented local download step, plus
  corpus expectations as data (`difficulty`, `pages`, `approx_size`,
  `features`, `sha256`).
- `ir/test_ir.py`, `ir/test_markdown_projection.py` — IR invariants and the
  deterministic Markdown projection (incl. nested children).
- `backends/test_registry.py`, `test_example_backend.py`,
  `backends/test_ocr.py` — backend protocol, registry precedence, third-party
  example, OCR/VLM adapters (heavy stacks stubbed through `sys.modules`,
  fully offline).
- `routing/test_classifier.py`, `providers/test_pdfinspector.py` — the OCR-need
  classifier seam and its pdf-inspector provider (`pdf_inspector` stubbed
  through `sys.modules`; the live 1-based indexing test skips without the
  extra).
- `backends/test_native_code_layout.py` — layout recovery v1 (pc-0uv): font
  classification, vendor-dictionary mapping, paragraph/CODE grouping,
  indentation and wrapped-line joins as pure unit tests, plus two
  `importorskip` integration tests that need the AGPL `pdf` extra.
- `providers/test_typesafe_ai.py` — the System One judge family against the
  offline SDK stub: spec/credential/extra failures, endpoint injection, the
  distribution-as-ranking contract, the `(intent, candidates)` memo, machine
  state, and a live tier gated on the `auto` extra plus
  `TYPESAFE_API_KEY`.
- `providers/test_jev_endpoints.py` — the Zen and Ollama endpoints (key
  requirement vs none, base URLs, the raised local timeout) plus the
  semi-live tier: a real local Ollama daemon under three machine profiles,
  with one behavioral anchor (a CPU-only host must not rank a GPU-only
  candidate first). Semi-live tests skip unless the `auto` extra and the
  daemon are both present.
- `cli/test_judges.py` — the `judges` catalog command (human + JSON output,
  extra/credential availability, and the docs-table check).
- `cli/test_verbosity.py` — the third-party output policy (quiet by default,
  untouched with `--verbose`, state restored afterwards).
- `test_smoke.py` — CLI surface (`backends --json`, the device column, `--version`,
  bare invocation shows help) and the output-encoding policy (pc-edn: a cp1252
  stdout must not fail a projection).
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

- **Boundary code gets real-object tests.** Where a test exercises a seam that
  talks to an outside engine (a backend adapter's handoff, a judge wire call, a
  storage format), construct the outside object with the **library's own types**
  guarded by `pytest.importorskip` — a stub proves the wiring, not the interface.
  Never take a fixture's shape as the library's shape: a recorded CLI JSON dump
  is not the in-memory object. `mise smoke` runs every opt-in CLI path against
  the real engines as a second net (issue #7 shipped behind stub-only coverage).
- New tests land in the group submodule matching their `src/parsecraft/`
  package as `test_<feature>.py`; only repo-wide invariants go to the
  `tests/` root. Group submodules are packages (`__init__.py`).
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
- Never weaken `fail_under = 100` (`pyproject.toml`) or the gates in
  `test_offline_import.py` — the offline package-import check **and** the
  collection-safety sweep (every `tests/**/test_*.py` re-imported with the
  heavy extras blocked; pc-eo7; recursive since the group layout).

## Verification

`mise test` (100% coverage) · `mise run all` (test + lint + spell + format +
format-md + docs) · `mise run test-corpus` (network tier, ≤ 15 min cold).

## Child DOX Index

None — leaf boundary.
