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

- Deterministic: identical input (and fixed `produced_at`) → byte-identical
  `DocumentResult`; chunk ids derive from `reading_order` (`md-NNNN-kind`).
- No silent drops: every level-0 token with content becomes a chunk (known
  kind or `unknown`); contentless tokens are recorded as an `INFO`
  diagnostic, never dropped invisibly.
- Token → `ChunkKind` mapping: heading (metadata `heading_level`),
  paragraph, list (metadata `list_type`), table, fence (metadata
  `language`), blockquote → quote, `$$…$$` paragraph → formula, image-only
  paragraph → figure, image + text → figure + caption, unhandled → unknown.
- Markdown is one page (`page_number=1`); `source_span`s are exact character
  offsets computed from the original text.
- Optional deps never import at module import time: front modules use
  `import_module` + typed `Protocol` seams and raise `MissingDependencyError`
  with an actionable hint. Impl modules are the only place their dep is
  imported, at module top level (csort-compatible).

## Verification

`mise test` — `tests/test_adapters_markdown.py`,
`tests/test_adapters_encoding.py`, `tests/test_adapters_http.py`
(100% coverage gate applies).
