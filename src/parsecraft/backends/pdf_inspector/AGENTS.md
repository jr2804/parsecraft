# AGENTS.md — src/parsecraft/backends/pdf_inspector/

pdf-inspector backend family: MIT Rust/PyO3 PDF extraction behind the optional
`pdf-inspector` extra (prebuilt `cp38-abi3` wheel, zero Python dependencies).

## Purpose

Expose `pdf-inspector` as a `DocumentBackend`: per-page Markdown extraction plus
the library's own per-page OCR verdict as planner signals.

## Ownership

- `pdf_inspector.py` — light factory (`PdfInspectorFactory`),
  `PDF_INSPECTOR_FORMATS`, `DESCRIPTOR`, `PDF_INSPECTOR_BACKEND_VERSION`.
  Imports no `pdf_inspector` code.
- `_impl.py` — heavy implementation (`PdfInspectorBackend`, `create`); the only
  module importing `pdf_inspector`, loaded via `importlib.import_module` at
  instantiation — never inline (`pyreorder` hoists inline imports and breaks the
  offline-import gate).
- `__init__.py` — package docstring only (entry point lives in `pdf_inspector.py`).

## Local Contracts

- Verified against pdf-inspector 1.25.2 (2026-10-01): the library is PDF-only,
  so `supported_formats` is `application/pdf` alone. Upstream page indexing is
  inconsistent — `PageMarkdown.page` and `classify_pdf`'s `pages_needing_ocr`
  are **0-indexed**, `process_pdf`'s `pages` argument is **1-indexed**. Convert
  to 1-based IR page numbers in one place (`page.page + 1`).
- `convert()` runs one whole-document pass (`extract_pages_markdown_bytes`) and
  filters to `page_range` while mapping (docling's shape): the real page count
  decides whether a range is out of bounds, because pdf-inspector answers an
  out-of-range page index with an empty **phantom page** that must never reach
  the IR. A range past the end is a typed `INVALID_INPUT`.
- `analyze()` reads the same per-page Markdown: length → `text_chars`,
  `needs_ocr` → `has_native_text`, U+FFFD share → `replacement_char_ratio`.
  `classify_pdf` is deliberately NOT the analyzer — it reports no per-page text
  volume, and a fabricated `text_chars` would route native pages to OCR.
- **Public surface is the factory + `convert()` (+ the protocol's `analyze`).**
  No `classify_pdf` pass-through: the pre-router classifier that needs it imports
  the library in its own provider (beads `pc-rzm`/`pc-1ow`) and does not consume
  this backend. `process_pdf` is not usable for `convert()` either — it returns
  one unattributeable Markdown blob for the whole document (verified 2026-10-01:
  `process_pdf(pages=[1])` selects the first page but still yields a single
  string), so per-page IR pages and `page_range` would be fabricated.
- One `PARAGRAPH` chunk per non-empty page (`pdf-inspector-p<n>-b0`,
  `metadata={"backend": "pdf-inspector"}`) carrying that page's Markdown;
  content-less pages stay present as empty `PageResult`s so the executor's
  exact-page check holds. Upstream table/column hints stay unconsumed for now.
- Never call pdf-inspector's OCR entry points (`process_pdf_with_ocr*`): this
  extra is the no-OCR path, `model_asset` stays `None`, and nothing downloads.
- Rule 10: the `pdf-inspector` extra resolves jointly with every other extra
  (zero Python dependencies); re-verify if upstream packaging changes.
- `EXTRA_IMPORTS` in `src/parsecraft/environment/probe.py` maps this extra to the
  `pdf_inspector` import — a group missing there is undetectable, hence unrouteable.

## Verification

`mise test` — `tests/test_backends_pdf_inspector.py` (offline; the heavy
extension is a stub in `sys.modules`), 100% coverage gate. Live smoke:
`uv add --optional pdf-inspector "pdf-inspector>=1.25.2"` in a throwaway
checkout, then convert a text-based PDF.
