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
- `pdf_text.py` — PyMuPDF extraction (`pdf`, AGPL — ADR-0003): maps each page's
dictionary into typed positioned lines, nothing else.
- `code_layout.py` — layout recovery v1 (pc-0uv): every rule that turns positioned
lines into PARAGRAPH/CODE chunks. Light and pure, so it is unit-tested with the
AGPL extra absent; `pdf_text` only hands it geometry.

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
- **Layout recovery v1** (pc-0uv) — `native-pdf` emits real `CODE` chunks, so the
  Markdown projection fences code instead of burying it in prose:
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
- `native-text`/`native-markdown`/`native-html` keep `paragraph_chunks`
  (`_common.py`); only `native-pdf` goes through `code_layout`.
- Synthetic PDF fixtures come from `tests/fixtures/documents.py` — no PDF
  bytes are ever committed (ADR-0001 §8). `code_pdf()` is the positioned-code
  fixture: prose plus a Courier listing at explicit x offsets.

## Verification

`mise test` — `tests/test_backends_native.py` and
`tests/test_native_code_layout.py` (100% coverage gate applies). The two
integration tests in the latter need the AGPL `pdf` extra and therefore skip in
the canonical light env; run them with
`uv run --isolated --extra pdf --extra pdf-lite pytest -k code_block_is_fenced`.
