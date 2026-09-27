# AGENTS.md — src/parsecraft/adapters/

Input adapters: convert external document formats into the canonical IR.

## Purpose

Parse source documents (currently Markdown via markdown-it-py) into
`DocumentResult`/`PageResult`/`StructuredChunk`. Adapters are INPUT-only:
they never consume rendered output of `parsecraft.ir.markdown` — the IR is
the single source of truth (ADR-0001 §11, `parsecraft.ir.AGENTS.md`).

## Ownership

- `markdown.py` — `parse_markdown(source: str | bytes, source_uri, ...) -> DocumentResult`
  and the internal token-stream builder.

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
- `markdown-it-py` (and every future heavy adapter dependency) imports
  lazily inside functions, never at module import time (offline-import gate).

## Verification

`mise test` — `tests/test_adapters_markdown.py` (100% coverage gate applies).
