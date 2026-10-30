# AGENTS.md — src/parsecraft/adapters/

Input adapters: convert external document formats into the canonical IR.

## Purpose

Parse source documents into `DocumentResult`/`PageResult`/`StructuredChunk`
and feed document bytes to future web backends. Adapters are INPUT-only:
they never consume rendered output of `parsecraft.ir.markdown` — the IR is
the single source of truth (ADR-0001 §11, `parsecraft.ir.AGENTS.md`).

## Ownership

- `markdown.py` — `parse_markdown(source: str | bytes, source_uri, ...) -> DocumentResult`
  (markdown-it-py, core dep, top-level import).
- `encoding.py` — `parse_content_type(header) -> (media_type, charset)` (stdlib)
  and `decode(data, declared_charset=None) -> EncodingDetection`
  (charset-normalizer behind an `import_module` seam).
- `http.py` — `fetch(url, ...) -> HttpResource` (httpx behind an
  `import_module` seam, `web` extra; raw bytes only, no decoding).
- `charset_impl.py`, `httpx_impl.py` — impl modules with TOP-LEVEL optional
  imports; loaded only by their light front modules at call time.
- `errors.py` — `AdapterError` hierarchy (`MissingDependencyError`,
  `HttpFetchError`).

## Local Contracts

- **Markdown tables carry structured `rows`** (pc-4u7.40): the parser keeps
  the row/cell lists it already builds and passes them to the TABLE chunk;
  the separator line lives only in `content`. Round-trip rule: flattening
  `rows` equals the non-separator cells of `content` (pinned in tests).
- Deterministic: identical input (and fixed `produced_at`) → byte-identical
  `DocumentResult`; chunk ids derive from `reading_order` (`md-NNNN-kind`).
- No silent drops: every level-0 token with content becomes a chunk (known
  kind or `unknown`); contentless tokens are recorded as an `INFO`
  diagnostic, never dropped invisibly.
- Token → `ChunkKind` mapping: heading (metadata `heading_level`),
  paragraph, list — one chunk PER ITEM (metadata `list_type`,
  `item_index`; nested lists stay flattened-attached to the parent item,
  pc-3vo), table, fence (metadata `language`), blockquote → quote,
  `$$…$$` paragraph → formula, image-only paragraph → figure,
  image + text → figure + caption, unhandled → unknown.
- Leading YAML-style frontmatter (`---` … `---`/`...`, ≥1 top-level `key:`
  line) becomes ONE verbatim `unknown` chunk with metadata
  `{"frontmatter": "true"}` (tolerance not repair — reversible,
  pc-3vo); anything break-shaped tokenises normally. Chunk `md-0000-*`,
  body `reading_order` starts at 1; post-fence `source_span`s shift by the
  fence length (pinned for LF/CRLF/bare-fence).
- Markdown is one page (`page_number=1`); `source_span`s are exact character
  offsets computed from the original text.
- Optional deps never import at module import time: front modules use
  `import_module` + typed `Protocol` seams and raise `MissingDependencyError`
  with an actionable hint. Impl modules are the only place their dep is
  imported, at module top level (pyreorder-compatible).

## Verification

`mise test` — `tests/adapters/test_markdown.py`,
`tests/adapters/test_markdown_regression.py` (pc-3vo: pipe-heavy fixture
with a 1.0 s bound, list fidelity, frontmatter pins),
`tests/adapters/test_encoding.py`, `tests/adapters/test_http.py`
(100% coverage gate applies).
