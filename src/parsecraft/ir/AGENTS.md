# AGENTS.md — src/parsecraft/ir/

Canonical intermediate representation (IR) and Markdown projection.

## Purpose

Single source of truth for structured document data. Every backend converges
into these types; all outputs (Markdown, HTML, JSON, consumer trees) render
from them.

## Ownership

- `models.py` — the IR schema (enums, value objects, chunks, pages, document,
  trace/failure records). `SCHEMA_VERSION` lives here.
- `markdown.py` — deterministic projection (`to_markdown`,
  `render_page`, `render_block`).
- `__init__.py` — curated re-exports (keep `__all__` sorted, `RUF022`).

## Local Contracts

- Markdown is a projection, never a source of truth — do not add parsing of
  rendered output back into IR types.
- No silent data loss: `to_markdown` must emit every block of every page,
  **including nested `children`** (pinned by
  `test_no_silent_drops_every_content_appears` and
  `test_no_silent_drops_nested_children`).
- Value objects (`BBox`, `SourceSpan`, `PageRange`, `PassFailure`, …) are
  frozen and validate in their `model_validator`s; failures raise
  `ValidationError` — never soft-return.
- `PassFailure` is a typed record, never a string (ADR-0001 §7);
  `TraceEntry` couples `status=failed` ↔ `failure` bijectively.
- `DocumentResult` enforces unique ascending pages, `page_count` consistency,
  and globally unique chunk ids (including nested children).
- Projection must stay deterministic and dependency-free: same
  `DocumentResult` → byte-identical Markdown.

## Verification

`mise test` — `tests/test_ir.py`, `tests/test_markdown_projection.py`,
`tests/typing_consumer.py` (ty-checked).
