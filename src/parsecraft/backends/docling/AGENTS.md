# AGENTS.md — src/parsecraft/backends/docling/

Docling backend family: MIT, CPU, layout-aware parsing behind the optional
`docling` extra.

## Purpose

Expose `docling` (2.130.0) as a `DocumentBackend`: cheap structural `analyze()`
plus a bound-checked `convert()` that maps docling items to typed IR chunks.

## Ownership

- `docling.py` — light factory (`DoclingFactory`), `DOCLING_FORMATS`,
  `DESCRIPTOR`, `DOCLING_BACKEND_VERSION`. Imports no docling code.
- `libreoffice.py` — LibreOffice discovery (`find_libreoffice_cmd`,
  `resolve_libreoffice_cmd`, `configure_libreoffice_env`,
  `LibreOfficeUnavailableError`); light and stdlib-only, so discovery is
  testable without the `docling` extra.
- `_impl.py` — heavy implementation (`DoclingBackend`, `create`); the only
  module that imports `docling`, `docling_core`, and `pypdfium2`.
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
- **LibreOffice discovery (pc-ct9)** lives in `libreoffice.py` and follows the
  environment-probe discipline: detect, never estimate. The operator's
  `DOCLING_LIBREOFFICE_CMD` is taken VERBATIM (a declaration is a decision,
  never probed); Windows then scans a bounded set of Program Files locations
  (`LibreOffice*/program/soffice.exe` — two roots, one non-recursive listing
  each, no drive-letter guessing); every other platform is PATH-only. Docling
  reads that variable itself, so setting it is the entire wiring — `create()`
  calls `configure_libreoffice_env()`, which never raises: no DECLARED format
  needs LibreOffice, because DOCX is read natively by docling (declared
  2026-10-17, verified on a real Word-generated report) and the remaining office
  formats stay undeclared until verified the same way. A missing LibreOffice
  only matters for a format that would need it, which is why nothing here
  depends on it today.
- `supported_formats` lists only inputs **conversion-verified** against the
  real library (PDF/HTML: 2026-09-28, docling 2.130.0; DOCX:
  2026-10-17, a Word-generated report read through this backend's CLI).
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
  page-number check holds.
- Bounds are honored between items: cancellation, `timeout_s` (checked after
  the single blocking pass), and `max_output_chars`. Failures stay typed
  (`CANCELLED`, `TIMEOUT`, `BUDGET_EXCEEDED`, `BACKEND_ERROR`), never raw.
- Rule 10: the `docling` extra must resolve jointly with every other extra —
  no `[tool.uv] conflicts`, no exception pins. Verified by dry-run resolution
  at `transformers==5.17.0` / `torch==2.14.0` (2026-09-28); re-verify if
  docling's pins move.

## Verification

`mise test` — `tests/backends/test_docling.py` (offline; the heavy import is a
stub in `sys.modules`) and `tests/backends/test_docling_libreoffice.py` (pure
stdlib, no extra needed). Live smoke: `uv run --isolated --extra docling
parsecraft convert FIXTURE` in a throwaway environment — dependencies are never
installed with `uv pip install`, the project routes dependency changes through
`uv add` (root rule), and `--isolated --extra` is the idiom for exercising a
real conversion.

On-demand measurement (never part of `mise test`/CI, resumable):
`mise run bench-docling` / `scripts/bench_docling.py`; results live in
`docs/benchmarks/` (`benchmark-docling-vs-native.json`).
