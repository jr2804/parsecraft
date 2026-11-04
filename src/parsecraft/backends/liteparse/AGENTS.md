# LiteParse Backend

Document conversion via the LiteParse library — cheap text-layer analyse and
per-page paged convert. Pure-Python extras (no AGPL/GPL, no GPU).

## Local Contracts

- **Format options flow through `BackendConfig.options`.** The CLI maps
  `--backend-option KEY=VALUE` into the constraints' `backend_options` map;
  the constructor accepts `extract_text_metadata`, `extract_vector_graphics`,
  `extract_blocks`, `extract_images`, `image_mode`, `keep_headers_footers`,
  `preserve_small_text` (forwarded to the LiteParse constructor; everything
  else is not ours to expose).
- **Item-level mapping is the default IR granularity for PDFs.** When
  `page.text_items` is present the adapter emits one chunk per logical unit
  (line → paragraph, font-derived heading level, running-header filter,
  dot-leader TOC suppression). When items are absent (older lit version, a
  format without items) the blob mapping is the fallback: one paragraph
  per page. The consumer's recorded 10-page ITU-T P.863 dump reproduces
  the prototype counts exactly (26 headings / 61 elements unfiltered;
  24 / 59 with the running-header filter, pinned in
  `tests/backends/test_liteparse_items.py`).
- **Figure surface (pc-usi, live-verified 2026-11-04).** The engine returns
  the document's images on the PARSE RESULT (`result.images`), not on the
  page, and only when the caller sets `extract_images`. Each image becomes
  one `ExtractedAsset` (payload as a `data:` URI, sha256 of the decoded
  bytes, the viewport rect as the bbox) plus one `FIGURE` chunk linked by
  `metadata["asset_id"]` — the shape `docling` and `mineru` emit. The option
  is off by default, so an existing conversion is unchanged.
- **Frames.** LiteParse reports the viewport frame: top-left origin, 72 DPI —
  which IS the IR's raster top-left frame, so the bbox is the rect as-is (no
  flip, unlike `docling`'s BOTTOMLEFT provenance). A figure's place in the
  reading order is its top edge against the text baselines (same frame).
- **No caption.** LiteParse supplies none for an extracted image, so the
  `FIGURE` chunk's content is empty; the chunk still exists, because a
  consumer attaches the image by id.
