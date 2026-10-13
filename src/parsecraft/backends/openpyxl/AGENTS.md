# openpyxl backend (XLSX)

## Purpose

Convert `.xlsx` workbooks into typed IR: **one `TABLE` chunk per worksheet**,
with the cell grid carried as structured `rows` and the sheet name as chunk
metadata. This is the spreadsheet backend; pymupdf deliberately does not claim
`.xlsx` because it extracts only numeric cells.

openpyxl is a **base dependency** (MIT, pure-Python), so this backend has no
extra, no licence gate, no model weights and no GPU.

## Ownership

Everything lives in one module, `openpyxl.py`: descriptor, factory, backend and
the grid helpers. There is deliberately **no light/heavy split** — the split
exists to defer an import, and the deferral here happens inside one module.

## Local contracts

- **The import is deferred, and that is a measured decision.** `import openpyxl`
  costs ~137 ms cold (against ~0.5 ms for a dependency-free factory), and
  registry discovery imports every entry point's factory module eagerly, so a
  module-scope import would add that to *every* command — including a PDF-only
  conversion. The module therefore resolves openpyxl through
  `import_module("openpyxl")` **in `__init__`**, which is also the one spelling
  `pyreorder` never hoists (POLICIES: imports must be hoist-safe). Keep the
  access behind the local `Protocol`s so the library stays out of the module's
  type surface.
- **No `ty: ignore[unresolved-import]`.** openpyxl is unconditional, so no
  suppression is needed and none may be added — an unused ignore is a
  warning-as-error in this repo.
- **Typed rows, never parsed Markdown** (root rule 4). Rows come from the
  worksheet grid; `content` is rendered *from* those rows for the projection.
  Nothing reads Markdown back into state.
- **One page.** A workbook has no pagination: the result is a single
  `PageResult` holding every sheet's table, `supports_page_ranges` and
  `supports_multi_page` stay off, and a range beyond page 1 is a typed
  `INVALID_INPUT` record.
- **Trailing blanks are trimmed** per sheet: all-blank trailing rows are
  dropped and trailing blank columns removed, so a grid is rectangular and
  carries no sheet-sized tail of empties. Interior blanks are preserved —
  an empty cell in the middle of a row is data.
- **The sheet name travels as `metadata["sheet"]`**, not as prose and not as a
  separate `CAPTION` chunk: the table stays one chunk per sheet.
- **Analyze is a size proxy** (the `pandoc`/`native` precedent): one page,
  `text_chars = len(data)`, so a text-bearing workbook never escalates to OCR.
- **`read_only=True, data_only=True`**: no formula evaluation and no writes back
  to the file, and a formula cell yields its cached value.
- Failures are typed records, never raises, except `source_bytes` on an
  unreadable source (the family's shape).
