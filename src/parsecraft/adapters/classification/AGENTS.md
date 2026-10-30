# AGENTS.md — src/parsecraft/adapters/classification/

## Purpose

Per-file text-kind classification for ambiguous text blobs: one decision-model
call per file selects the structure parser (markdown-like → `parse_markdown`,
data-like → the native-csv parser, else the plain paragraph path).

## Ownership

- `__init__.py` — `TextKind`, `TextKindClassifier`/`TextKindDecisionModel`
  Protocols, typed errors, spec parse/register/resolve, `classify_text_kind`,
  `parse_ambiguous_text`.
- `_systemone.py` — `SystemOneDecisionModel` (Choice call, SDK behind a
  call-time `import_module` seam).

## Local Contracts

- Verdicts are the closed `TextKind` set (`markdown-like`/`data-like`/`plain`).
- `MARKDOWN_LIKE` → `parse_markdown` (source format forced `text/plain`);
- One model call per file, full text untruncated; blank input costs zero calls.
- Any failure degrades to `(PLAIN, 0.0)` — never out of the pipeline.
- The verdict lands on chunk `detected_kind` metadata, the first-class
  `confidence` field (never a same-named metadata key), and a `text-kind`
  page diagnostic; `DATA_LIKE` → the native-csv parser (TABLE IR, sniffed
  delimiter, csv diagnostics preserved; csv-path failure falls back to plain
  with its own `text-kind` INFO diagnostic naming the failure type only)
- The csv backend module is imported call-time: its import raises
  `field_size_limit`, which a text-only process must not pay for unasked.
  (verdict preserved for it).
- `metadata.format` always stays the source type (`text/plain` on both
  branches) — the verdict must never re-key identical bytes.
- The plain path reuses `paragraph_chunks` with the `native-text` id prefix,
  so chunk identity is identical with or without a classifier.
- Determinism: same bytes with the same model answer yield the same verdict;
  ties and unparsable answers resolve to PLAIN.
- Provider token is never hardcoded: `None` resolves the configured
  text-classifier (`[text_classifier] model` is the only activation surface;
  the routing judge config never activates it, pc-9p6.5).

## Verification

`mise test` — `tests/adapters/test_classification.py` (100% gate applies).

## Child DOX Index

None.
