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
- **No LibreOffice discovery** (removed pc-wl3.4, 2026-10-31): docling.rs
  parses office formats natively in Rust and ignores
  `DOCLING_LIBREOFFICE_CMD` (verified — a real DOCX converts with the
  variable pointing at garbage), so no declared format needs LibreOffice
  and `create()` configures nothing.
- **Constructor options are honoured (pc-fwn).** `BackendConfig.options`
  reaches the engine through `_CONVERTER_OPTION_KEYS`, a curated subset of the
  `DocumentConverter` keywords — not the ~80-key surface, so upstream additions
  do not silently become our API (liteparse's rule). Unknown keys RAISE: a
  dropped option looks like success, and an operator debugging a memory problem
  would be reading a config that never arrived. Booleans coerce through
  `backends.options.coerce_option_value` and are type-checked; `create()`
  validates eagerly, so a bad value fails before a conversion starts.
- **`offline` is injected into every backend's option map** by
  `pipeline/executor.py` and is NOT a `DocumentConverter` keyword — forwarding
  the map wholesale dies with `TypeError: … unexpected keyword argument
  'offline'` (verified against docling-rs 2.55.4). `_INJECTED_OPTION_KEYS`
  accepts and drops it; the structural fix (a typed field on `BackendConfig`)
  is a separate, wider change.
- **One converter per option set, not per process.** `_CONVERTERS` is keyed by
  the resolved options: docling.rs loads its pipelines inside the converter, so
  a shared instance would serve the second caller the first caller's switches.
- **`do_ocr=false` empties scanned pages silently in the engine** (no warning at
  all — verified), so the adapter supplies `docling-ocr-disabled` for exactly
  the pages it emptied. Distinct from `docling-ocr-unavailable` (models
  missing): the fix is a different one. The executor's
  `degraded-empty-output` skips a page that already carries diagnostics, so the
  specific fact wins and the two never double-speak.
- `supported_formats` lists only inputs **conversion-verified** against the
  real library (PDF/HTML/DOCX: re-verified 2026-10-31 against docling.rs
  2.55.4 through this backend — PDF with models installed, page_range
  honored exactly).
  Legacy Word 97-2003 binary (`.doc`, `application/msword`) joined them
  2026-11-06 on the same evidence — two real OLE2 Word files (an English
  Word 95 document and a Chinese-language one with a heading, a numbered list
  and a table) read natively by docling.rs 2.65.1 with no LibreOffice and no
  models, and through this backend's own CLI path they land as one page with
  4 and 8 typed blocks (paragraph, list, table).
  Markdown and plain text were verified the same way but are withdrawn
  (2026-10-30): both reach docling's marko table matcher, which spins without
  timeout on pipe-heavy content, so they route to ``native-markdown`` /
  ``native-text`` instead — enforced twice: the tuple no longer lists them
  (eligibility-level routing away) and ``convert()`` refuses them with a typed
  ``BACKEND_ERROR`` naming the redirect target (defense-in-depth for direct
  registry use). The library's other registered inputs (pptx, xlsx, odt, ods,
  odp, rtf, xls, images) stay undeclared until a conversion proves them —
  mirror LiteParse's contract. The legacy binary trio's other two members
  (`.ppt` / `.xls`) are registered upstream and stay undeclared for the same
  reason: a legacy PowerPoint fixture converted to ZERO items and a legacy
  spreadsheet fixture to one near-empty table.
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
- **Figure surface (pc-sc9, live-verified 2026-11-03).** Cropped
  `PictureItem`s (an `image` with a `data:` URI — present by default,
  gone under `keep_picture_images=false`) become one `ExtractedAsset`
  plus one `FIGURE` chunk linked by `metadata["asset_id"]` — the same
  shape `mineru` emits. The asset URI is the engine's crop `data:` URI
  verbatim; `sha256` hashes the DECODED payload bytes; the bbox comes
  from the item's provenance flipped out of docling's BOTTOMLEFT (y-up)
  frame using page heights pypdfium2 reads separately (the engine
  exposes no page sizes; a pypdfium2 failure — e.g. a PDF docling
  repaired — degrades to `bbox=None`, never a dropped asset).
  Uncropped/unusable pictures are skipped entirely (no dangling chunk);
  a document without figures is unchanged. A figure chunk counts as a
  block, so a figure-bearing page does NOT trigger the empty-page
  diagnostics below — those claim the page "came back empty", which a
  page with a figure did not (assets alone never mute them).
- **Missing models** map to a typed `BACKEND_ERROR` naming
  `docling_rs.download_models()` (matched on the `model not found at`
  substring of the real `ConversionError`, verified with an empty model
  cache). Models are never downloaded implicitly (about 1.4 GB measured
  into the docling.rs model cache, not the ~700 MB upstream claims).
  **This covers the LAYOUT models only.** A cache with the layout models but
  without the OCR ones does not raise at all: docling-rs prints a warning on
  stderr, continues without OCR, and returns pages with zero blocks (pc-04u,
  verified 2026-10-31 by deleting only `ocr_*` from a full cache — a text PDF
  through the same cache is unaffected, so the two paths differ and only one
  is typed). The adapter now surfaces that degradation: `convert()` captures
  fd 2 around the single blocking pass (`dup2` to a temp file under a
  process-global lock, replayed verbatim afterwards) and, when the bytes
  contain `_OCR_UNAVAILABLE_FRAGMENT` ("OCR model unavailable" — kept next to
  the layout fragment constant with the same fragment-not-sentence comment,
  same upstream-wording coupling class), attaches a `WARNING` page diagnostic
  `docling-ocr-unavailable` naming `docling_rs.download_models()` to every
  page the degradation emptied (zero-block pages only; healthy pages stay
  clean, a text PDF through the same cache carries no warning). The signal
  is a page diagnostic, deliberately **not** a second `quality` entry: the
  router's `degraded-blank-page` already fires from page-signal
  classification on both empty and OCR'd outcomes, so a second global entry
  could double-report one page. Live verification:
  `tests/backends/test_docling_live.py` (opt-in `--run-live-docling`, every
  conversion in its own child process — docling-rs shares loaded models
  between live processes, measured).
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
`sys.modules`). Live smoke: `uv run --isolated --extra docling
parsecraft convert FIXTURE` in a throwaway environment — dependencies are never
installed with `uv pip install`, the project routes dependency changes through
`uv add` (root rule), and `--isolated --extra` is the idiom for exercising a
real conversion. The figure surface (pc-sc9) is additionally covered by the
opt-in live tier: `pytest tests/backends/test_docling_live.py
--run-live-docling` runs the real engine on a synthesized chart page (figure
asset + linked chunk) and a text-only page (no figure surface), skipping
gracefully without the model cache.

On-demand measurement (never part of `mise test`/CI, resumable):
`mise run bench-docling` / `scripts/bench_docling.py`; results live in
`docs/benchmarks/` (`benchmark-docling-vs-native.json`).
