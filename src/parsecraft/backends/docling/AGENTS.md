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
  needs LibreOffice (office formats stay undeclared until conversion-verified),
  so a host without it still converts PDF/HTML/Markdown/text. Hosts asking for
  LibreOffice explicitly get the typed `LibreOfficeUnavailableError`, naming the
  environment variable. The Windows branch cannot run on a Linux CI runner
  (memory #1127): tests drive it with a synthetic Program Files tree.
- `supported_formats` lists only inputs **conversion-verified** against the
  real library (2026-09-28, docling 2.130.0): `application/pdf`,
  `text/html`, `text/markdown`, `text/plain`. docling also registers
  docx/pptx/xlsx/odt/ods/odp/rtf/xls and image formats, but they stay
  undeclared until conversion-verified — mirror LiteParse's contract.
- `analyze()` is cheap and model-free: PDF page count and text length come
  from `pypdfium2` (a docling dependency), text formats are measured directly.
  It never loads layout models and never downloads.
- `convert()` runs one `DocumentConverter` pass, passes `page_range` only for
  PDF (`convert(page_range=...)`), maps items per page (`TableItem` →
  `export_to_markdown`, `TextItem` labels → `ChunkKind`), and returns every
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

`mise test` — `tests/test_backends_docling.py` (offline; the heavy import is a
stub in `sys.modules`) and `tests/test_backends_docling_libreoffice.py` (pure
stdlib, no extra needed). Live smoke: `uv run --isolated --extra docling
parsecraft convert FIXTURE` in a throwaway environment — dependencies are never
installed with `uv pip install`, the project routes dependency changes through
`uv add` (root rule), and `--isolated --extra` is the idiom for exercising a
real conversion.

On-demand measurement (never part of `mise test`/CI, resumable):
`mise run bench-docling` / `scripts/bench_docling.py`; results live in
`docs/benchmarks/` (`benchmark-docling-vs-native.json`).
