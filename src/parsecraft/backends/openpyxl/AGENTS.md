# openpyxl backend (XLSX)

## Purpose

Convert `.xlsx` workbooks into typed IR: **one `TABLE` chunk per worksheet**,
with the cell grid carried as structured `rows` and the sheet name as chunk
metadata. This is the spreadsheet backend; pymupdf deliberately does not claim
`.xlsx` because it extracts only numeric cells.

## Ownership

- Format list, capabilities and the descriptor: `openpyxl.py` (the light
  module the entry point names).
- Extraction and chunk construction: `_impl.py` (imports `openpyxl` at module
  scope, loaded only through the factory's `importlib` boundary).
- Fixtures and tests: `tests/fixtures/documents.py` (`xlsx_bytes()`) and
  `tests/backends/test_openpyxl.py`.

## Local contracts

- **Typed rows, never parsed Markdown** (root rule 4). Rows come from the
  worksheet grid; `content` is rendered *from* those rows for the projection.
  Nothing reads Markdown back into state.
- **One page.** A workbook has no pagination: the result is a single
  `PageResult` holding every sheet's table, `supports_page_ranges` and
  `supports_multi_page` stay off, and a range beyond page 1 is a typed
  `INVALID_INPUT` record.
- **Trailing blanks are trimmed** per sheet: all-blank trailing rows are
  dropped and trailing blank columns are removed, so a grid is rectangular and
  carries no sheet-sized tail of empties. Interior blanks are preserved —
  an empty cell in the middle of a row is data.
- **The sheet name travels as `metadata["sheet"]`**, not as prose and not as a
  separate chunk: the table stays one chunk per sheet.
- **Analyze is a size proxy** (the `pandoc`/`native` precedent): one page,
  `text_chars = len(data)`, so the declared surface is analyzable on a base
  install and routing never escalates a text-bearing workbook to OCR.
- **`read_only=True, data_only=True`**: no formula evaluation and no writes back
  to the file, and cell values are read as stored (a formula's cached value).
- Failures are typed records, never raises, except `source_bytes` on an
  unreadable source (the family's shape).
