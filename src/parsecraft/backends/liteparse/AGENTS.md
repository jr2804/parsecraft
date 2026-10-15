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
