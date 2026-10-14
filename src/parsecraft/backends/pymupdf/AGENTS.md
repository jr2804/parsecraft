# AGENTS.md — src/parsecraft/backends/pymupdf/

PyMuPDF backend: pypdf inspection (core) + PyMuPDF extraction (`pdf` extra,
AGPL — ADR-0003) behind the backend contract.

## Purpose

Expose the PyMuPDF path as a `DocumentBackend`: cheap structural `analyze()` on
core pypdf plus a bound-checked `convert()` via PyMuPDF's text dictionary
when the `pdf` extra is installed. The runtime is the only third-party dep in
the `native-*` family; the prefix was dropped and the package was split out
to keep the native family light-by-construction.

## Ownership

- `__init__.py` — descriptor + factory (`PymupdfBackend`, `_PymupdfFactory`,
  `PymupdfFactory`, `BACKEND_NAME`, `SUPPORTED_FORMATS`, the two module-path
  constants for the `import_module` seams).
- `_analyze.py` — pypdf inspection (core, no extra). Counts image XObjects
  on each page so scanned PDFs surface a non-zero `image_count` signal.
- `_convert.py` — PyMuPDF extraction (`pdf` extra, AGPL): maps each page's
  text dictionary into positioned `PageText` lines, then defers to
  `code_layout.build_chunks` for the layout-recovery rules.
- `code_layout.py` — layout recovery v1 (pc-0uv): every rule that turns
  positioned lines into PARAGRAPH/CODE chunks. Light and pure, so it is
  unit-tested with the AGPL extra absent; `_convert` only hands it geometry.

## Local Contracts

- Entry point: `parsecraft.backends.pymupdf:factory`; heavy imports load
  through `importlib.import_module` at `_analyze_impl()` (instantiation) and
  `_convert_pdf` (conversion time), never inline (`pyreorder` would hoist an
  inline import to module level and break the offline-import gate).
- Descriptor: `BACKEND_NAME = "pymupdf"`, `convert_extra = "pymupdf"`, no
  `optional_dependency_group` because analysis is core; extraction requires
  `parsecraft[pymupdf]` and surfaces a typed `DEPENDENCY_MISSING` pass failure if
  it is absent.
- **One raster surface, zero PyMuPDF at import** (pc-ct9 / ADR-0003): the
  factory raises if the analyze import fails, but never tries to import
  PyMuDF at import time. The `pdf` extra is the only gate.
- **Layout recovery v1** (pc-0uv) — emits real `CODE` chunks, so the Markdown
  projection fences code instead of burying it in prose:
  - CODE is a run of consecutive **monospace-majority** lines (`LINE_CODE_RATIO`);
    monospace is a font-name fact (`MONOSPACE_MARKERS` minus
    `MONOSPACE_EXCLUSIONS` — `Monotype` contains "mono" and is proportional).
  - Indentation is *relative to the block's leftmost glyph*, in estimated columns
    (`SPACE_WIDTH_RATIO` × size, clamped). PDF positions glyphs: full column
    reconstruction needs per-glyph advances and is deliberately out of scope.
  - Wrapped lines join only when the next line is not dedented and the previous
    one lacks a `STATEMENT_TERMINATORS` ending (the POLQA `unsigned long` +
    `mulMode;` shape).
  - PARAGRAPH behaviour is unchanged: a paragraph breaks on a blank line, whether
    the text layer spells one or only leaves the vertical gap.
  - A page whose CODE mass reaches `CODE_MASS_THRESHOLD` carries a `feature:code`
    INFO diagnostic. That code is **IR-only**: `analyze()` runs on pypdf and sees
    no fonts, so the planner cannot consume it and no intent rule exists — code
    pages keep routing NATIVE.
  - Determinism holds as everywhere else: same bytes → identical chunks.
- `pymupdf` extraction NEVER uses pypdf; pypdf inspection NEVER uses PyMuPDF.
  The two paths are independent, and a cache-absent `pdf` extra degrades
  `convert()` to a typed failure without affecting `analyze()`.
- `Structured table rows` (pc-4u7.40): TABLE chunks carry `rows` built from
  the projected markdown grid; pymupdf writes them through the same builder
  used by `native-html` so cells and content agree. Non-table chunks carry
  `rows=None` — extraction is plain text with no cheap grid structure.
- Idempotency: same bytes → identical chunks/pages; ids are
  `pymupdf-<page>-b<n>`.

## Verification

`mise test` — `tests/backends/test_pymupdf.py` and
`tests/backends/test_pymupdf_code_layout.py` (100% coverage gate applies). The
two integration tests in the latter need the AGPL `pdf` extra and therefore
skip in the canonical light env; run them with
`uv run --isolated --extra pymupdf pytest -k code_block_is_fenced`.
