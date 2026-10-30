# AGENTS.md — src/parsecraft/adapters/classification/

## Purpose

Per-file text-kind classification for ambiguous text blobs: one decision-model
call per file selects the structure parser (markdown-like → `parse_markdown`,
else the plain paragraph path).

## Ownership

- `__init__.py` — `TextKind`, `TextKindClassifier`/`TextKindDecisionModel`
  Protocols, typed errors, spec parse/register/resolve, `classify_text_kind`,
  `parse_ambiguous_text`.
- `_systemone.py` — `SystemOneDecisionModel` (Choice call, SDK behind a
  call-time `import_module` seam).

## Local Contracts

- Verdicts are the closed `TextKind` set (`markdown-like`/`data-like`/`plain`).
- One model call per file, full text untruncated; blank input costs zero calls.
- Any failure degrades to `(PLAIN, 0.0)` — never out of the pipeline.
- The verdict lands on chunk `detected_kind` metadata, the first-class
  `confidence` field (never a same-named metadata key), and a `text-kind`
  page diagnostic; `DATA_LIKE` parses plain until delimited ingestion lands
  (verdict preserved for it).
- `metadata.format` always stays the source type (`text/plain` on both
  branches) — the verdict must never re-key identical bytes.
- The plain path reuses `paragraph_chunks` with the `native-text` id prefix,
  so chunk identity is identical with or without a classifier.
- Determinism: same bytes with the same model answer yield the same verdict;
  ties and unparsable answers resolve to PLAIN.
- Provider token is never hardcoded: `None` resolves the configured decision
  model (`--judge` beats `PARSECRAFT_JUDGE_MODEL` beats `[judge] model`).

## Verification

`mise test` — `tests/adapters/test_classification.py` (100% gate applies).

## Child DOX Index

None.
