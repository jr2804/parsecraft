# ParseCraft JSON output reference

Both machine-readable surfaces of the CLI: the conversion result
(`convert --json`, the full IR) and the routing preview (`inspect --json`).
JSON is written to stdout with 2-space indent and sorted keys; errors go to
stderr with a non-zero exit code.

## Contents

- [convert --json — DocumentResult](#convert---json--documentresult)
  - [Top level](#top-level)
  - [metadata](#metadata)
  - [pages[]](#pages)
  - [blocks[] — StructuredChunk](#blocks--structuredchunk)
  - [Chunk kinds](#chunk-kinds)
  - [relations\[\] and quality\[\]](#relations-and-quality)
  - [trace\[\]](#trace)
- [inspect --json — routing preview](#inspect---json--routing-preview)
  - [analysis](#analysis)
  - [plan](#plan)
- [Invariants consumers may rely on](#invariants-consumers-may-rely-on)
- [Related CLI surfaces](#related-cli-surfaces)

## convert --json — DocumentResult

```bash
parsecraft convert report.pdf --json > result.json   # or set PARSECRAFT_JSON=1
```

Real top-level shape (keys sorted):

```json
{
  "metadata": { "source_uri": "...", "source_hash": "ae96…", "format": "text/markdown",
                "page_count": 1, "title": null, "produced_at": "2026-10-18T…",
                "package_version": "2026.10.x", "schema_version": "…" },
  "pages": [ … ],
  "quality": [],
  "relations": [],
  "trace": [ … ]
}
```

### Top level

| Key | Type | Meaning |
| --- | --- | --- |
| `metadata` | object | Document identity and reproducibility anchors |
| `pages` | array | One entry per source page, ascending, unique |
| `relations` | array | Cross-page relations (`kind`, `source_id`, `target_id`, `source_page`, `target_page`) |
| `trace` | array | One entry per conversion pass (backend provenance) |
| `quality` | array | Document-level quality signals (`name`, `score` 0–1, optional `detail`, `page_number`) |

### metadata

| Field | Type | Notes |
| --- | --- | --- |
| `source_uri` | string | Where the document came from |
| `source_hash` | string | SHA-256 of the source bytes |
| `format` | string | Resolved media type, e.g. `text/markdown` |
| `page_count` | int | Always equals `len(pages)` |
| `title` | string \| null | Document title when available |
| `produced_at` | datetime | UTC production timestamp |
| `package_version` | string | parsecraft version that produced the result |
| `schema_version` | string | IR schema version |

### pages[]

| Field | Type | Notes |
| --- | --- | --- |
| `page_number` | int ≥ 1 | Unique, ascending across the document |
| `width`, `height` | float \| null | Page geometry when the backend reports it |
| `blocks` | array | `StructuredChunk`s in document order (`reading_order` per page) |
| `regions` | array | Detected regions: `id`, `kind`, `bbox`, `confidence` |
| `assets` | array | Extracted assets: `id`, `kind` (`image`/`latex`/`csv`/`other`), `media_type`, `uri`, `bbox` |
| `diagnostics` | array | `level` (`info`/`warning`/`error`), `code`, `message` |

### blocks[] — StructuredChunk

Real block emitted for an `# ParseCraft` heading:

```json
{
  "id": "md-0000-heading",
  "kind": "heading",
  "content": "ParseCraft",
  "page_number": 1,
  "reading_order": 0,
  "bbox": null,
  "source_span": {"start": 0, "end": 13},
  "children": [],
  "relations": [],
  "confidence": null,
  "quality": [],
  "rows": null,
  "metadata": {"heading_level": "1"}
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `id` | string | Globally unique, including nested children |
| `kind` | enum | See [chunk kinds](#chunk-kinds) |
| `content` | string | The chunk's text (Markdown projection renders from this) |
| `page_number` | int ≥ 1 | Owning page |
| `reading_order` | int ≥ 0 | Order within the page |
| `bbox` | object \| null | `x0`, `y0`, `x1`, `y1` when the backend reports geometry |
| `source_span` | object \| null | `{start, end}` offsets into the source when applicable |
| `children` | array | Nested chunks (headings over paragraphs, cells over content, …) |
| `relations` | array | Same-page relations: `kind`, `target_id` |
| `confidence` | float 0–1 \| null | Backend-reported confidence |
| `quality` | array | Per-chunk quality signals (`name`, `score`, `detail`, `page_number`) |
| `rows` | array of arrays \| null | Consumer data for `kind == "table"` only — the real cell grid |
| `metadata` | object of strings | Kind-specific extras, e.g. `heading_level`, `sheet` (spreadsheets) |

### Chunk kinds

`heading`, `paragraph`, `list`, `table`, `formula`, `code`, `figure`,
`caption`, `header`, `footer`, `quote`, `unknown`.

### relations and quality

`relations[]` (document level): `kind` — `RelationKind` — plus `source_id`,
`target_id`, `source_page`, `target_page`.

`quality[]`: `name`, `score` (0–1), optional `detail` and `page_number` — the
same signal shape repeats inside pages' signals, blocks, and diagnostics.

### trace[]

Real entry (native pass over Markdown):

```json
{
  "backend": "native-markdown",
  "backend_version": "0.1.0",
  "pass_kind": "native",
  "status": "ok",
  "elapsed_s": 0.0038,
  "page_range": null,
  "settings": {"intent": "native"},
  "failure": null
}
```

| Field | Notes |
| --- | --- |
| `pass_kind` | Which pass produced this (`native`, OCR, fallback, …) |
| `status` | `ok` or failed — a failed status couples bijectively with a `failure` |
| `page_range` | `{start, end}` when the pass covered a page range, else `null` |
| `settings` | Effective pass settings (strings) |
| `failure` | Typed record when `status` failed: `code`, `pass_kind`, `backend`, `backend_version`, `budget_s`, `elapsed_s`, `detail`, `occurred_at`, optional `page_range` |

Failure `code` values: `timeout`, `budget_exceeded`, `dependency_missing`,
`gpu_insufficient`, `model_unavailable`, `cancelled`, `backend_error`,
`invalid_input`.

## inspect --json — routing preview

```bash
parsecraft inspect report.pdf --json
```

Analyzes the source and shows the planned routing without converting — cheap
enough to run before every batch. Real output:

```json
{
  "media_type": "text/markdown",
  "analysis": {
    "page_count": 1,
    "source_hash": "ae9645e7c5b9868799631604b608e7fec1e4a0fc85fe0066d0de409eb0c9955e",
    "diagnostics": [],
    "signals": [
      {"page_number": 1, "has_native_text": true, "text_chars": 4782, "image_count": 0,
       "blank": false, "replacement_char_ratio": null, "classifier_needs_ocr": null}
    ]
  },
  "plan": {
    "primary": "native-markdown",
    "pages": [
      {"page_number": 1, "intent": "native", "candidates": ["native-markdown"],
       "chosen": "native-markdown",
       "reason": "page 1: native text sufficient (text_chars=4782); first pass native-markdown",
       "degradation_code": null, "degradation_score": null}
    ]
  },
  "routing_error": null
}
```

### analysis

- `signals[]` — per-page routing evidence: `has_native_text`, `text_chars`,
  `image_count`, `blank`, `replacement_char_ratio` (0–1 of replacement chars in
  garbled text), `classifier_needs_ocr` (null unless a `--classifier` ran).
- `diagnostics[]` — analysis-level `level`/`code`/`message` entries.
- `source_hash` — matches `metadata.source_hash` of a later conversion of the
  same bytes (the cache key starts from it).

### plan

- `primary` — the lead backend for the document.
- `pages[]` — per-page decision: `intent`, `candidates` (eligible ordered),
  `chosen`, human-readable `reason`, and optional `degradation_code`/`-score`
  when a page was degraded (e.g. garbled/short/blank pages falling back).
- `routing_error` — non-null on analysis/routing failure (exit code `1`).

## Invariants consumers may rely on

1. Pages are unique and strictly ascending; `metadata.page_count == len(pages)`.
2. Chunk `id`s are globally unique across the document, nested children included.
3. The Markdown projection (stdout without `--json`) is byte-identical for the
   same `DocumentResult` — but never parse it back; use `--json`.
4. Exit code `0` means success — only then is stdout a result. Codes `1`
   (analysis/routing failure) and `2` (usage error) put typed messages on stderr.
5. Failed runs are never cached, so `--cache` never masks a failure.

## Related CLI surfaces

- `parsecraft backends --json` — machine-readable backend descriptors
  (formats, GPU/VRAM declarations, optional dependency groups).
- `parsecraft convert -o FILE` — write either projection to a file; the parent
  directory must already exist.
- `parsecraft judges` — judge providers, tokens, credentials (text table).
- Environment: `PARSECRAFT_JSON` (JSON without the flag), `PARSECRAFT_OFFLINE=1`
  (exclude model-carrying backends).
