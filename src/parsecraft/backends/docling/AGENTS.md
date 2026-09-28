# AGENTS.md — src/parsecraft/backends/docling/

Docling backend family: MIT, CPU, layout-aware parsing behind the optional
`docling` extra.

## Purpose

Expose `docling` (2.130.0) as a `DocumentBackend`: cheap structural `analyze()`
plus a bound-checked `convert()` that maps docling items to typed IR chunks.

## Ownership

- `docling.py` — light factory (`DoclingFactory`), `DOCLING_FORMATS`,
  `DESCRIPTOR`, `DOCLING_BACKEND_VERSION`. Imports no docling code.
- `_impl.py` — heavy implementation (`DoclingBackend`, `create`); the only
  module that imports `docling`, `docling_core`, and `pypdfium2`.
- `__init__.py` — package docstring only (entry point lives in `docling.py`).

## Local Contracts

- Entry point: `parsecraft.backends.docling.docling:factory`; heavy imports
  load through `importlib.import_module` at instantiation, never inline
  (`csort` would hoist an inline import to module level and break the
  offline-import gate).
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
stub in `sys.modules`). Live smoke: `uv pip install "docling>=2.130"` in a
throwaway venv and convert a fixture PDF/HTML/Markdown.
