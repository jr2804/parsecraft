# AGENTS.md — src/parsecraft/backends/native/

Built-in native backends: light document parsers behind the backend contract.

## Purpose

Four entry-point backends, each a light `factory` (descriptor + `__call__`):

| Backend | Module | Formats | Extra |
| ------- | ------ | ------- | ----- |
| `native-text` | `text.py` | `text/plain` | — |
| `native-markdown` | `markdown.py` | `text/markdown` | — |
| `native-html` | `html.py` | `text/html` | — |
| `native-pdf` | `pdf.py` | `application/pdf` | `pdf-lite` (analyze), `pdf` (extract) |

## Ownership

- `_common.py` — `NativeBackendBase` (analyze/convert flow: page range,
  cancellation, budgets, timeout, typed `PassFailure`s), `source_bytes`,
  paragraph helpers, `PdfInspection`/`PageTextStats`, typed errors.
- `pdf_inspect.py` — pypdf inspection (`pdf-lite`).
- `pdf_text.py` — PyMuPDF extraction (`pdf`, AGPL — ADR-0003).

## Local Contracts

- **Structured table rows** (pc-4u7.40): `native-html` emits `rows` from its
  `_row`/`_cell` buffers (same cells as the Markdown `content`, separator
  excluded). `native-markdown` inherits rows through the markdown adapter;
  `native-text`/`native-pdf` carry `rows=None` — their extraction is plain
  text with no cheap grid structure.
- Entry modules stay light: optional/heavy deps (`pypdf`, `pymupdf`) load via
  `import_module` seams only — `pdf_inspect` at factory instantiation,
  `pdf_text` at conversion time (missing extra → typed
  `DEPENDENCY_MISSING` pass failure, never an import crash). No inline
  imports (`pyreorder` hoists them).
- `analyze()` must work with only `pdf-lite` installed; the descriptor
  carries `optional_dependency_group="pdf-lite"`.
- Descriptors set `version=NATIVE_BACKEND_VERSION` (feeds
  `registry.fingerprint()`); `BackendRef` uses the same constant.
- `native-pdf` extraction NEVER uses pypdf; inspection NEVER uses PyMuPDF.
- Deterministic: same bytes → identical chunks/pages; ids are
  `native-<fmt>-<page>-b<n>` / `native-html-NNNN-<kind>`.
- HTML parses structure only (headings/paragraphs/lists/tables/code/quotes);
  skipped markup (`script`/`style`/`head`/`title`) and empty blocks are
  recorded as INFO diagnostics, never silently dropped.
- Synthetic PDF fixtures come from `tests/fixtures/documents.py` — no PDF
  bytes are ever committed (ADR-0001 §8).

## Verification

`mise test` — `tests/test_backends_native.py` (100% coverage gate applies).
