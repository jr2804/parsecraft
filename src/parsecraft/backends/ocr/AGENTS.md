# AGENTS.md — src/parsecraft/backends/ocr/

The OCR backend family: four heavyweight model backends behind `ocr-*` extras,
plus `ocr-tesseract`, the base-install backend that drives an OS binary
(ADR-0008).

## Purpose

Expose OCR as `DocumentBackend`s: a cheap, model-free `analyze()` and a
bound-checked `convert()` that maps recognized text into typed IR chunks — for
scanned pages and page images, which no native backend can read.

## Ownership

- `_common.py` — the shared machinery every OCR backend uses: the raster
  surface, `analyze_source`, `convert_pages` (all per-page bounds live here),
  `ensure_assets`, and the tokenizer/processor corrections VLM backends need.
- `_models.py` — the model inventory: names, pinned revisions, licences, file
  pins, and the capability records. No heavy imports; consumed by light
  factories and heavy impls alike.
- `ovis.py`, `tele.py`, `unlimited.py`, `qianfan.py` — light factories; the
  heavy work is in `_<name>_impl.py`.
- `tesseract.py` — the base-install backend AND its engine discovery. **There is
  no `_impl` split here, by design** (see the contracts).
- `_vendored/unlimited/` — upstream model code vendored for the unlimited
  backend.

## Local Contracts

- **One raster surface, one resolution.** `rasterize_page` / `count_pages` use
  the base `pypdfium2` engine and nothing else — PyMuPDF was removed under
  ruling B, so the AGPL `pdf` extra now gates only `native-pdf` extraction.
  Pages rasterize at `RASTER_DPI = 300` (Tesseract's documented minimum;
  accuracy falls off sharply below ~150), bounded by `MAX_RASTER_PIXELS =
  40_000_000`. Over the cap the scale is reduced **by area**, never by width, so
  the result lands exactly on the cap: A4–A2 raster at full 300 dpi, A1 at
  ~227 dpi, A0 at ~161 dpi — still above the ~150 floor. The cap emits **no
  diagnostic**: there is no convert-time per-page channel for a success-path
  fact, and it is a memory guard, not a routing decision.
  `tests/test_backends_ocr.py` pins the true rendered pixels of A4 against the
  **real** engine, so an engine swap cannot silently change the resolution.
- **`supported_formats` is a capability claim, never an aspiration.** Only list
  what conversion verified against the real runtime. That is why TIFF is in the
  shared surface (Tesseract reads it via Leptonica) but only `ocr-tesseract`
  declares `image/tiff`.
- **`ocr-tesseract` has no `_impl` split because there is nothing heavy to
  defer**: the engine is a binary, and discovery plus the subprocess call are
  stdlib. The module is light by construction, not by convention. It declares
  `gpu_requirement` NOT_NEEDED and `estimated_vram_gb` None, so it is eligible
  on a CPU-only host through the existing `is_hard_eligible` path — no new
  routing primitive.
- **Engine discovery is declaration-first, and the probe delegates to it.**
  `PARSECRAFT_TESSERACT` is honoured verbatim and never probed; then a bounded,
  non-recursive scan of `%ProgramFiles%`/`%ProgramFiles(x86)%`; then PATH.
  `environment/probe.py` calls `find_tesseract_cmd` through `ENGINE_DISCOVERY`
  instead of reimplementing the check, because tesseract's installer lets a user
  decline PATH registration — a PATH-only probe would report a working install
  as absent and drop the backend on exactly the hosts ADR-0008 targets.
  Eligibility and "the executable we actually run" must never disagree.
- **The engine binary is never fetched** (ADR-0008 decision 4, as amended):
  no upstream publishes a portable, hash-published release, so pulling would be
  unauditable. Absence is a `TesseractUnavailableError`, which derives from
  `DependencyUnavailableError` so the executor records `dependency_missing`
  rather than the misleading `backend_error`, and its message names the OS
  package per platform family.
- **System tessdata wins; the managed cache is the fallback.** `TESSDATA_PREFIX`
  first and alone, then a bounded packaging glob. "Complete" means complete
  **for the languages the backend invokes** (`eng`, `deu`) — requiring `equ`
  would defeat the purpose, since no OS package ships it.
- **The tessdata asset is deliberately NOT `capabilities.model_asset`.**
  `is_hard_eligible` drops any backend carrying a model_asset when
  `constraints.offline` holds, and offline DEFAULTS TRUE at the library level, so
  declaring it would strip the base OCR path from every embedder that had not
  explicitly declared online. The pins live on `TESSERACT_ASSET` and the fetch
  goes through `ensure_assets`, where an offline `AssetManager` refuses BEFORE
  any transfer (the pc-eoa pre-attempt pattern). Accepted cost: the CLI model
  catalogue is capabilities-driven, so this asset is absent from
  `parsecraft models list` and unaddressable by `models install/remove <name>`.
- **`equ` ships but is never selected automatically** (ADR-0008 decision 7).
  Page-type-dependent engine selection would be a new routing primitive for a
  bounded gain, and `equ` recognition is too weak to promise. Formula-critical
  documents belong with the heavyweight `ocr-*` extras.
- Per-page generation budget and tokenizer-kwargs contracts are unchanged and
  live in the parent (`../AGENTS.md`); read them there rather than restating
  them here.

## Verification

`mise all` — green, 100% coverage. The family's own suites are
`tests/test_backends_ocr.py` (the VLM adapters and the shared raster surface;
the heavy imports are stub modules in `sys.modules`) and
`tests/test_backends_tesseract.py` (discovery, engine invocation, tessdata
resolution — the engine is never actually executed).

Engine discovery is tested against a **synthetic host** (a temporary tree plus
the environment variables that point at it): each platform reaches only one
branch, so neither can be asserted by running on the other (memory #1127).
