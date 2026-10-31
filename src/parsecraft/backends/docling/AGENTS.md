# AGENTS.md — src/parsecraft/backends/docling/

Docling backend family: MIT, CPU, layout-aware parsing behind the optional
`docling` extra (engine: docling.rs via the `docling-rs` PyPI package, MIT;
upstream labels the Python bindings experimental).

## Purpose

Expose docling.rs (2.55.x) as a `DocumentBackend`: cheap structural `analyze()`
plus a bound-checked `convert()` that maps docling items to typed IR chunks.
`result.document` is a genuine docling-core `DoclingDocument`, so the item
mapping is engine-independent.

## Ownership

- `docling.py` — light factory (`DoclingFactory`), `DOCLING_FORMATS`,
  `DESCRIPTOR`, `DOCLING_BACKEND_VERSION`. Imports no docling code.
- `libreoffice.py` — LibreOffice discovery (`find_libreoffice_cmd`,
  `resolve_libreoffice_cmd`, `configure_libreoffice_env`,
  `LibreOfficeUnavailableError`); light and stdlib-only. RETIRED from the
  convert path 2026-10-31 (pc-wl3.3): docling.rs parses office formats
  natively in Rust — verified with a real DOCX converting while
  `DOCLING_LIBREOFFICE_CMD` pointed at garbage — so `create()` no longer
  calls it. The module and its tests stay (pure stdlib, still passing)
  until a follow-up removes them.
- `_impl.py` — heavy implementation (`DoclingBackend`, `create`); the only
  module that imports `docling_rs`, `docling_core`, and `pypdfium2`.
- `__init__.py` — package docstring only (entry point lives in `docling.py`).

## Local Contracts

- **Structured table rows** (pc-4u7.40): TABLE chunks carry `rows` built from
  `TableItem.data.grid` (`list[list[TableCell]]` → cell texts), while
  `content` remains the Markdown export — both derive from the same table,
  and tests pin cell agreement. Non-table items carry `rows=None`.
- Entry point: `parsecraft.backends.docling.docling:factory`; heavy imports
  load through `importlib.import_module` at instantiation, never inline
  (`pyreorder` would hoist an inline import to module level and break the
  offline-import gate).
- **LibreOffice discovery (pc-ct9)** lives in `libreoffice.py` but is RETIRED
  from the convert path (pc-wl3.3, see Ownership): docling.rs parses office
  formats natively in Rust and ignores `DOCLING_LIBREOFFICE_CMD` (verified
  2026-10-31 - a real DOCX converts with the variable pointing at garbage),
  so `create()` no longer calls `configure_libreoffice_env()`. No declared
  format needs LibreOffice.
- `supported_formats` lists only inputs **conversion-verified** against the
  real library (PDF/HTML/DOCX: re-verified 2026-10-31 against docling.rs
  2.55.4 through this backend — PDF with models installed, page_range
  honored exactly).
  Markdown and plain text were verified the same way but are withdrawn
  (2026-10-30): both reach docling's marko table matcher, which spins without
  timeout on pipe-heavy content, so they route to ``native-markdown`` /
  ``native-text`` instead — enforced twice: the tuple no longer lists them
  (eligibility-level routing away) and ``convert()`` refuses them with a typed
  ``BACKEND_ERROR`` naming the redirect target (defense-in-depth for direct
  registry use). The library's other registered inputs (pptx, xlsx, odt, ods,
  odp, rtf, xls, images) stay undeclared until a conversion proves them —
  mirror LiteParse's contract.
- `analyze()` is cheap and model-free: PDF page count and text length come
  from `pypdfium2` (a base dependency, ADR-0008), text formats are measured
  directly. It never loads layout models and never downloads.
- `convert()` runs one `DocumentConverter` pass, passes `page_range` only for
  PDF (`convert(page_range=...)`), maps items per page (`TableItem` →
  `export_to_markdown`, `TextItem` labels → `ChunkKind`; titles and section
  headers carry `metadata["heading_level"]` with the author's own level —
  title `"1"`, sections `item.level` verbatim clamped to 6, never the tree
  depth from `iterate_items()`), and returns every
  requested page — empty `PageResult`s included — so the executor's exact
  page-number check holds (`page_range=(1, 1)` yields page 1 only, verified
  against the real package).
- **Missing models** map to a typed `BACKEND_ERROR` naming
  `docling_rs.download_models()` (matched on the `model not found at`
  substring of the real `ConversionError`, verified with an empty model
  cache). Models are never downloaded implicitly (about 1.4 GB measured
  into the docling.rs model cache, not the ~700 MB upstream claims).
- Bounds are honored between items: cancellation, `timeout_s` (checked after
  the single blocking pass), and `max_output_chars`. Failures stay typed
  (`CANCELLED`, `TIMEOUT`, `BUDGET_EXCEEDED`, `BACKEND_ERROR`), never raw.
- Rule 10: the `docling` extra must resolve jointly with every other extra —
  no `[tool.uv] conflicts`, no exception pins (`docling-rs>=2.55` needs only
  `docling-core>=2.0` + `pydantic>=2.6,<3`; re-verify by dry-run resolution
  if its pins move).

## Verification

`mise test` — `tests/backends/test_docling.py` (offline; the heavy
`docling_rs` / `docling_core` / `pypdfium2` imports are stubs in
`sys.modules`) and `tests/backends/test_docling_libreoffice.py` (pure
stdlib, no extra needed). Live smoke: `uv run --isolated --extra docling
parsecraft convert FIXTURE` in a throwaway environment — dependencies are never
installed with `uv pip install`, the project routes dependency changes through
`uv add` (root rule), and `--isolated --extra` is the idiom for exercising a
real conversion.

On-demand measurement (never part of `mise test`/CI, resumable):
`mise run bench-docling` / `scripts/bench_docling.py`; results live in
`docs/benchmarks/` (`benchmark-docling-vs-native.json`).
