# AGENTS.md — src/parsecraft/backends/mineru/

MinerU backend family: **native** VLM document parsing. The `mineru` pip
package is gone — parsecraft runs the Apache-2.0 MinerU2.5 checkpoint itself
behind `parsecraft[mineru]` (ADR-0007, amended 2026-10-26).

## Purpose

Expose MinerU2.5-class PDF parsing as a `DocumentBackend`: a cheap, model-free
`analyze()` and a bound-checked per-page `convert()` that runs the two-step
VLM protocol (layout detection, then per-block recognition) and maps blocks to
typed IR chunks.

## Ownership

- `mineru.py` — light factory, `DESCRIPTOR`, `MINERU_ASSET` with the pinned
  file set. Imports nothing heavy.
- `_impl.py` — heavy implementation: engine load (stock `transformers`), the
  per-page conversion loop, block→IR mapping. Imports `torch`/`transformers`
  at module top by design (loaded only from the light factory).
- `_layout.py` — pure-python layout-stage parser (box/ref token grammar,
  dedupe, coverage filter). No imports beyond stdlib.
- `_content.py` — pure-python content post-processing: OTSL→HTML,
  markdown-table→HTML, equation bracketing, `[Non-Text]` handling, the
  image-analysis classifier. No imports beyond stdlib.

## Local Contracts

- **The extra is dependency-free (ADR-0007 amendment, 2026-10-26):**
  `parsecraft[mineru]` = `accelerate`, `torch`, `torchvision`,
  `transformers>=5.17,<6`. The `mineru` package (openai<3 cap, PR #3750 to
  lift it closed unmerged) is dropped — the extra now resolves jointly with
  every other extra AND with downstream model stacks (rule 10), including
  clean-room `uv tool` installs that cannot carry repository overrides.
- **Protocol ported, not vendored:** the two-step layout/recognition protocol
  is reimplemented from `mineru-vl-utils` (Apache-2.0, attribution in the
  module docstrings). Layout runs on a 1036×1036 raster with `<|box_start|>`/
  `<|ref_start|>` tokens; recognition crops blocks from the full-resolution
  raster. Prompt strings are parsecraft's own record of that protocol.
- **Effort is the VLM dial:** `flash` = text-layer-first via `pypdfium2`
  signals — weight-free for text PDFs, the engine loads lazily only for
  textless pages; `medium`/`high`/`xhigh` = VLM on every page, engine loaded
  eagerly at `create()` (fail fast), generation budget escalating
  (`EFFORT_BUDGETS` 2048/2048/4096/8192). A request's `max_context_tokens`
  beats the dial.
- **`page_range` is honored natively** — the per-page loop converts only the
  requested window (no whole-document pass, no post-filter). Page geometry:
  layout bboxes are fractions scaled by the page's point size.
- **Asset discipline:** every checkpoint file is pinned (`file_pins`) and the
  download is owned by parsecraft (`ensure_assets`) into the managed revision
  directory — visible to `parsecraft models list`, reclaimable by `models
  clean`/`remove`. `options["offline"]` refuses before any transfer; offline
  eligibility follows the cached-assets marker
  `opendatalab/MinerU2.5-Pro-2605-1.2B@<revision>`.
- **Declared from measurement:** `gpu_requirement` GPU_OPTIONAL (0.5 — CPU
  works, slowly), `estimated_vram_gb` 4.0 (1.2B BF16 ≈ 2.3 GiB weights plus
  activations). A GPU host is preferred by routing, never required.
- **Cross-family reuse:** the raster surface and the tokenizer/processor
  load machinery are imported from `backends/ocr/_common` — one raster
  surface across families (300 dpi, `MAX_RASTER_PIXELS` cap; see
  `../ocr/AGENTS.md`).
- **Bounds are per-page:** cancellation checked between pages, `timeout_s`
  before and after each page, a per-page exception becomes one typed
  `BACKEND_ERROR` failure and the loop continues, `max_output_chars` stops
  the loop before overflow. Failures stay typed, never raw.
- `supported_formats` lists only conversion-verified inputs:
  `application/pdf` (native path, 2026-10-26).
- No multiprocessing anywhere — the docvortex spawn hazard died with the
  package, and its guard test with it.

## Verification

`mise run test` — green (1451 passed, 100% coverage, 2026-10-26). The
family's suites are `tests/backends/test_mineru.py` (engine seam, conversion
loop, mapping, bounds — heavy imports are stub modules in `sys.modules`) and
`tests/backends/test_mineru_layout.py` (pure parser grammar).

Live smoke (never CI): `uv run --isolated --extra mineru parsecraft convert
FIXTURE` in a throwaway environment — dependencies route through `uv add`
only (root rule).
