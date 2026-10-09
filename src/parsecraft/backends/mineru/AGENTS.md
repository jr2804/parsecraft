# AGENTS.md — src/parsecraft/backends/mineru/

MinerU backend family: conditional-licence opt-in extra, VLM document parsing
behind `parsecraft[mineru]` (ADR-0007).

## Purpose

Expose `mineru` (4.0.11) as a `DocumentBackend`: a cheap, model-free
`analyze()` and a bound-checked `convert()` that maps MinerU's Content List
items to typed IR chunks.

## Ownership

- `mineru.py` — light factory (`MinerUFactory`), `MINERU_FORMATS`, `DESCRIPTOR`,
  `MINERU_BACKEND_VERSION`. Imports no mineru code.
- `_impl.py` — heavy implementation (`MinerUBackend`, `create`); the only module
  that imports `mineru`, `docvortex`, and `pypdfium2`.
- `__init__.py` — package docstring only (entry point lives in `mineru.py`).

## Local Contracts

- **The extra is the opt-in** (ADR-0007): `parsecraft[mineru]` resolves the whole
  55-package stack and parsecraft owns the pin. This is the *opposite* shape
  from `marker` (backend + entry point, no extra — ADR-0006); the two must not
  be documented identically. The licence statement ships in the same change as
  the extra — never a silent install.
- Entry point: `parsecraft.backends.mineru.mineru:factory`; heavy imports load
  through `importlib.import_module` at instantiation, never inline
  (`pyreorder` would hoist an inline import to module level and break the
  offline-import gate).
- `supported_formats` lists only inputs **conversion-verified** against the real
  library (2026-10-09, mineru 4.0.11): `application/pdf`. MinerU also handles
  docx/pptx/xlsx and images upstream; they stay undeclared until
  conversion-verified — mirror the docling/LiteParse contract.
- **Page indexing is normalized in exactly one place** (house rule):
  `_page_number` maps MinerU's 0-based `page_idx` to the IR's 1-based
  `page_number`. MinerU 4.x carries two index conventions in one release
  (0-based `page_idx` in the content list vs 1-based `page:{page_no}` in the
  doclib locators); the adapter reads `page_idx` and never the locators.
- `analyze()` is cheap and model-free: page count, per-page text length, and
  page size come from `pypdfium2` (a declared docvortex dependency, so no new
  extra is needed). It never loads a model and never downloads.
- `convert()` runs one `doc_analyze` pass and maps Content List items by type
  (`text`, `table`, `equation`, `image`, `index`, `header`, `footer`,
  `page_number`) to `ChunkKind`. It returns every requested page — empty
  `PageResult`s included — so the executor's exact page-number check holds.
- **`page_range` is post-filtered, not forwarded.** MinerU's range-input base is
  delegated downstream and unverified, so the adapter converts the whole
  document and filters the result to the requested pages. Correct, but a
  narrow range on a large document still costs a full pass.
- **`effort="flash"` + `parse_mode="txt"` is the weight-free path**: MinerU
  skips model loading entirely for a text PDF at flash effort, so no checkpoint
  is downloaded and no VRAM is consumed. Verified: an 80-page text PDF converts
  in ~20 s with the model cache directory still empty. `image_analysis=True`
  enables docvortex's visual-crop stage and is the only path that rasterizes.
- **Spawn guard (mandatory test):** docvortex renders page images through
  `multiprocessing.get_context("spawn")`, and spawn re-imports the parent's
  `__main__` as `__mp_main__`. An unguarded module body therefore re-executes
  the whole workload inside every render worker. The backend's own module body
  is import-safe (no top-level executable code), and
  `tests/test_backends_mineru.py::test_console_script_spawn_guard_survives_docvortex`
  pins it end to end with a negative control.
- Bounds are honored: cancellation, `timeout_s` (checked after the single
  blocking pass), and `max_output_chars`. Failures stay typed (`CANCELLED`,
  `TIMEOUT`, `BUDGET_EXCEEDED`, `BACKEND_ERROR`), never raw.
- **Cache discipline:** `MINERU_HOME` is a revision directory of the managed
  cache (`<cache>/models/<slug>/<revision>`), so the self-fetched weights are
  visible to `parsecraft models list` and reclaimable by `models
  clean`/`remove`; a model is downloaded once per host rather than per run, and
  third-party loader progress bars are muted so the CLI output stays readable.
- **Offline:** `options["offline"]` is read before any model work; a declared
  offline run refuses a checkpoint download instead of attempting it.
- Rule 10: the `mineru` extra must resolve jointly with every other extra —
  no `[tool.uv] conflicts`, no exception pins. Verified by dry-run resolution
  on Python 3.13 and 3.14 (2026-10-09); re-verify if mineru's pins move.

## Verification

`mise all` — green (1163 passed, 100% coverage, 2026-10-09). The
backend's own suite is `tests/test_backends_mineru.py` (offline; the heavy
imports are stub modules in `sys.modules`). The spawn test is extra-gated: it
skips unless `mineru` and `pypdf` are both importable, because it needs a real
render pass.

Live smoke (never part of `mise test`/CI): `uv run --isolated --extra mineru
parsecraft convert FIXTURE` in a throwaway environment — dependencies are never
installed with `uv pip install`, the project routes dependency changes through
`uv add` (root rule), and `--isolated --extra` is the idiom for exercising a
real conversion.
