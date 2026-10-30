# AGENTS.md — src/parsecraft/backends/native/

Built-in native backends: light document parsers behind the backend contract.

## Purpose

Four entry-point backends, each a light `factory` (descriptor + `__call__`):

| Backend | Module | Formats |
| ------- | ------ | ------- |
| `native-text` | `text.py` | `text/plain` |
| `native-markdown` | `markdown.py` | `text/markdown` |
| `native-html` | `html.py` | `text/html` |
| `native-csv` | `csv.py` | `text/csv`, `text/tab-separated-values` |

The PDF backend is the separate ``pymupdf`` package (``backends/pymupdf/``) — it
moved out of ``native/`` because it is the only built-in that needs a third-party
runtime (PyMuPDF, the ``pdf`` extra, AGPL — ADR-0003). The ``native-`` prefix
is now reserved for backends with no third-party runtime dep.

## Ownership

- `_common.py` — `NativeBackendBase` (analyze/convert flow: page range,
  cancellation, budgets, timeout, typed `PassFailure`s), `source_bytes`,
  paragraph helpers, typed errors.

## Local Contracts

- **Structured table rows** (pc-4u7.40, pc-oir.1): `native-html` emits `rows` from its
  `_row`/`_cell` buffers (same cells as the Markdown `content`, separator
  excluded). `native-markdown` inherits rows through the markdown adapter;
  `native-csv` emits `rows` from the parsed cell grid — header stays row 0
  and is never lifted into metadata (consumer contract: the consumer keys
  data rows by column name). `native-text` carries `rows=None` — its
  extraction is plain text with no cheap grid structure. CSV delimiter sniffing
  samples logical records (including multiline quoted cells); only empty
  physical tail lines are trimmed, not explicit empty or whitespace cells.
  CSV Markdown escapes structural pipes, line breaks and literal HTML; `rows`
  remains authoritative because other Markdown inline formatting can render.
- `native-text` classifies only when a text-kind classifier resolves
  (pc-9p6.3): unconfigured runs emit bare `paragraph_chunks` (no verdict key,
  `confidence=None`, byte-identical to before); a resolved classifier stamps
  `detected_kind` plus real confidence via `parse_ambiguous_text`; data-like
  verdicts use the CSV parser, and Markdown-like verdicts the Markdown adapter.
  Resolution or parse failures fall back to the bare path with an INFO
  `text-kind` diagnostic — ingestion is never blocked. Only a malformed spec
  (fixable by the operator) keeps a WARNING; an unavailable provider is our
  wiring gap, not the user's mistake. Extension routing in `media_type_for`
  is untouched: a classifier only improves structure within plain text,
  never refuses it.
- Entry modules stay light: optional/heavy deps (none here — see
  `backends/pymupdf/AGENTS.md` for the pymupdf import boundaries). No inline
  imports (`pyreorder` hoists them).
- `analyze()` works on the core install; the descriptor carries no
  `optional_dependency_group` (pypdf is a core dependency).
- Descriptors set `version=NATIVE_BACKEND_VERSION` (feeds
  `registry.fingerprint()`); `BackendRef` uses the same constant.
- HTML parses structure only (headings/paragraphs/lists/tables/code/quotes);
  skipped markup (`script`/`style`/`head`/`title`) and empty blocks are
  recorded as INFO diagnostics, never silently dropped.
- `native-text`/`native-markdown`/`native-html` keep `paragraph_chunks`
  (`_common.py`); PDF layout recovery lives in ``pymupdf/code_layout.py``.
- The PDF-only synthetic fixtures come from `tests/fixtures/documents.py` — no
  PDF bytes are ever committed (ADR-0001 §8). ``code_pdf()`` is the
  positioned-code fixture: prose plus a Courier listing at explicit x offsets.

## Verification

`mise test` — `tests/backends/test_native.py` (100% coverage gate applies).
PDF coverage is in `tests/backends/test_pymupdf.py` and
`tests/backends/test_pymupdf_code_layout.py`; the two integration tests in the
latter need the AGPL `pymupdf` extra and therefore skip in the canonical light env;
run them with `uv run --isolated --extra pymupdf pytest -k code_block_is_fenced`.
