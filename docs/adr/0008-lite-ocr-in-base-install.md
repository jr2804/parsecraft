# ADR-0008: A lightweight OCR backend in the base install (tesseract, system binary + pulled assets)

- **Status:** Accepted
- **Date:** 2026-10-09
- **Deciders:** user (ruling relayed via knox-1), pc-1
- **Related:** bead `pc-utm` (recon `.agents/plans/ocr-lite/00-recon.md`),
  ADR-0005 (system binary, GPL pandoc), ADR-0006 (bring-your-own dependency),
  ADR-0007 (conditional-licence opt-in extra), ADR-0001 amendment (free-threaded
  cells suspended), root AGENTS.md rules 3 and 10

## Context

The user ruled that parsecraft's **base** install must ship a lightweight OCR
backend — not another heavyweight `ocr-*` extra. The need is concrete: a
consumer that consolidates onto parsecraft and installs only
`parsecraft[liteparse,pdf-lite,docling,pandoc]` has **no OCR engine at all**, so
scanned pages degrade to the native path and produce nothing but a
`degraded_pages` trace. Required properties: no GPU, no network at run time, and
small enough to be a default.

The recon (`pc-utm`) established the constraint set:

- **`onnxruntime` — the only small, permissive, CPU-capable wheel engine — cannot
  be a base dependency.** It publishes `cp314t` wheels for Linux only and no
  sdist, so `uv sync` fails outright on free-threaded macOS/Windows (proved by a
  live dry-run). PEP 508 has no free-threaded marker, so neither an optional
  dependency nor a self-referential core extra can escape that cell; carrying it
  as an extra is also **latently rule-10-violating** (the extras gate runs on
  3.13 only, so it would ship green and break a free-threaded host).
- **`tesseract` as a system binary is the only matrix-safe candidate.** Apache-2.0
  binary *and* Apache-2.0 `tessdata` (so, unlike pandoc, **no copyleft
  addendum**), `eng`+`deu`+`equ`+`osd` fast models = 5.37 MB, and it is not a
  wheel, so no interpreter cell can fail on it.
- `rapidocr` (Apache-2.0 code and weights, all three models bundled in the wheel,
  no runtime fetch) has better accuracy-per-MB but **no German** — PP-OCRv4's
  repertoire is zh/en/ja with a 63-character alphanumeric charset, so `ä ö ü ß`
  are absent, which is a correctness gap for a German corpus, not a quality one.
- Eliminated: `paddlepaddle` (no cp314 wheel), `easyocr` (~800 MB torch plus a
  runtime weight download), `ocrmypdf` (MPL-2.0, wrong tool), `tesserocr` (no
  Windows wheels).

The user also ruled that when an asset is not installable through pyproject, the
implementation must **pull it on demand**, and that the free-threaded CI cells
are suspended for now (ADR-0001 amendment) rather than repricing the interpreter
tier for one engine's packaging gap.

## Decision

1. **Ship shape (c): a base-registered OCR backend over the `tesseract` system
   binary.** The *backend* is in the base and eligible by default; the *engine*
   is an OS package. No new extra is declared for it.
2. **Engine discovery is a probe, not an assumption** (ADR-0005 pattern,
   `backends/docling/libreoffice.py` as the template): an env-var override first,
   then a bounded platform search (PATH on POSIX; the standard install roots on
   Windows), then a typed, actionable `DependencyUnavailableError` naming the OS
   package to install. A declaration is honoured verbatim and never probed.
3. **Assets are pulled on demand through the managed cache.** The backend
   declares the `tessdata` files it needs (`eng`, `deu`, `equ`, `osd`) as its
   **`model_asset`**, with pinned upstream URLs (GitHub `tesseract-ocr/tessdata_fast`)
   and sha256 per file. That routes them through the existing machinery — managed
   cache, `parsecraft models list/install`, the first-use download notice, and the
   offline exclusion — which is exactly what marker and mineru could not use
   because they fetch their own weights. System-provided tessdata wins when it is
   present and complete (`TESSDATA_PREFIX`), so a host with an OS install
   downloads nothing.
4. **The engine binary itself is discovered first, and never fetched.** If it is
   absent, the typed, actionable `DependencyUnavailableError` is the **only**
   outcome — the OS package manager is the install path, and the error names the
   package per platform family (`tesseract-ocr` / `tesseract` / choco+winget).

   **Amended 2026-10-09 after the implementation verified the upstream:** the
   original text promised an opt-in pull "from a pinned, hash-verified upstream
   release", and **no such release exists on any platform** — the official
   `tesseract-ocr/tesseract` 5.5.3 ships a single Windows *installer*
   (`tesseract-ocr-w64-setup-5.5.3.20260724.exe`, 26,573,224 B), UB-Mannheim
   v5.4.0.20240606 the same shape (50,175,248 B), Linux and macOS have distro
   packages only, and the sole per-platform binaries anywhere are conda-forge
   rebuilds (203 files with per-file sha256) packaged for conda and needing
   dependency extraction to become runnable. Both candidates are *worse* than
   what this decision protects against: silently running a third-party installer
   is a larger supply-chain action than a hash-verified binary, and a conda
   rebuild is a rebuild-of-a-rebuild. A pull seam with no artifact to pull would
   be dead code, which this codebase removes on principle.

   **Re-entry condition:** a portable, hash-published upstream release. If one
   appears, either pull it directly or add the operator-supplied pinned URL +
   sha256 variant (considered and deliberately deferred: it puts the trust
   decision at the operator boundary, which is the right place for a third-party
   executable, but it needs a new config surface and nothing can use it today).
5. **No new routing primitives.** `INTENT_RULES` and `can_degrade_to_native` are
   unchanged; the backend declares no GPU requirement and no VRAM, so it is
   eligible on a CPU-only host through the existing `is_hard_eligible` path. The
   only routing change is observational: `planner._degraded_reason` gets a
   distinct string for *engine missing* versus *no OCR backend installed*, so a
   host-configuration problem stops reading like a routing failure.
6. **`model_asset` is declared for the pulled assets, `None` for anything the
   backend does not download.** Weight provenance is recorded in the descriptor.
7. **Formula handling: `equ` ships with the pulled set and the limitation is
   documented.** No page-type-dependent engine selection in v1 — that is a new
   decision primitive for a bounded gain; formula-critical documents stay with the
   heavyweight `ocr-*` extras.
8. **The accuracy path is documented, not built:** a marker-style
   **bring-your-own** `rapidocr` backend (no extra declared → rule 10 untouched)
   is the rule-10-safe way to offer it later, with its German gap and its
   free-threaded unavailability stated where it is offered.
9. **Acceleration stays optional and user-provided.** The base engine is CPU;
   heavier or acceleratable engines remain in the `ocr-*` extras, and any
   accelerator runtime a user installs (ONNX Runtime providers, OpenVINO, a
   Vulkan-class engine) is their own install — no new primitives, no new probe
   facts in v1.
10. **The base gains a permissive PDF raster surface: `pypdfium2`, which
    REPLACES the PyMuPDF raster path.** Scanned-PDF OCR needs page rasterization,
    and the only one we had was `_common.py`'s PyMuPDF surface — the AGPL `pdf`
    extra (ADR-0003 opt-in). Putting AGPL on the default path is not acceptable,
    so the base depends on `pypdfium2` (plus `Pillow`, its documented bridge to
    encoded images — decision 14) and the shared raster surface uses it alone.
    Verified at 5.14.0: licence **BSD-3-Clause + Apache-2.0** (permissive, no
    copyleft addendum), and 22 of 23 published files are
    **`py3-none-<platform>`** — ABI-independent, so unlike `onnxruntime` this
    dependency carries no interpreter-version risk (it installs on the GIL tiers
    and would install on free-threaded builds too).

    **The PyMuPDF fallback is deliberately NOT kept** (amended 2026-10-09 after
    the implementation review): a base dependency cannot be absent, so the
    fallback's only firing condition would be "pypdfium2 fails on a PDF that
    PyMuPDF can parse" — a case with no evidence, paid for with a second engine's
    stubs, tests and a dual channel in the one place that must be deterministic.
    One raster engine, always present, is the honest design; unreachable branches
    are removed rather than kept behind a condition nobody can take.

    The AGPL `pdf` extra survives and is not orphaned: `native/pdf_text.py`,
    `native/pdf.py` and `native/code_layout.py` import PyMuPDF directly for
    native-PDF text extraction. Its raster-related wording is what changed —
    OCR no longer needs it, and the old "pip install 'parsecraft[pdf]'" failure
    path disappears with the fallback.

## Amendment (2026-10-09): implementation rulings (pc-3rp design)

Decisions 11-14, taken while reviewing `.agents/plans/ocr-lite/01-design.md`.
They record how decisions 3, 5 and 10 above are realised; none changes a
principle, and the environment/asset mechanisms they reuse are the ones already
in the tree.

11. **Tessdata source: the OFFICIAL upstream, not a mirror.**
    `ModelAssetDescriptor` gains a defaulted `model_source` discriminator and a
    `GitHubDownloader` (stdlib `urllib`, no new dependency) is selected in
    `ensure_assets`. The HF mirror available today is missing `equ` — i.e. it
    would silently void decision 7 — and provenance for a hashed asset belongs
    with the upstream that publishes it, not a third-party re-tag. Existing
    descriptors are unaffected (the field is defaulted) and the URL+sha256 pin
    discipline is identical across sources.
12. **`allow_ocr` derives from host facts, not from heavyweight extras.**
    `None` means "an `ocr-*` extra is installed **or** a usable engine exists for
    a registered OCR backend". Deriving it from the extras alone would exclude
    the base backend on exactly the hosts decision 1 exists for; enabling OCR
    whenever a base backend merely exists would route pages into a family that
    then cannot run.
13. **Engine presence is a host fact, generally named.**
    `BackendDescriptor.required_engine: str | None` plus
    `EnvironmentInfo.engines: frozenset[str]` (probed; `tesseract` only today),
    with one `is_hard_eligible` condition: a backend declaring a required engine
    is ineligible when that engine is absent. This is the `gpu_usable` /
    `installed_extras` pattern — a probe fact flowing to one eligibility check —
    not a new routing primitive: `INTENT_RULES` and `can_degrade_to_native` are
    untouched, and no backend name is special-cased. The same fact feeds
    decision 12, so one mechanism serves both. Pandoc and the docling LibreOffice
    path are the obvious future members; they are deliberately **not** migrated
    here.
14. **The base gains `Pillow` alongside `pypdfium2`.** It is pypdfium2's own
    documented bridge to encoded images (`to_pil`), already a dependency of the
    OCR extras and docling, and licence-permissive; hand-rolling an image encoder
    to avoid it would be the shim this project rejects. Consequence, to be stated
    rather than discovered: the shared raster surface serves **all** OCR
    backends, so the existing `ocr-*` adapters move to the pypdfium2 surface,
    which is now the only raster engine (see decision 10).

Decision 5's planner distinction (`engine missing` vs `no OCR backend
installed`) is realised through decision 13, not by moving the signal into the
executor: a host-configuration problem must be visible where routing decisions
are explained.

## Alternatives considered

- **(a) True base dependency on a wheel engine** and **(b) a self-referential core
  extra**: both rejected — the free-threaded macOS/Windows cell cannot resolve,
  and (b) buys no coverage the matrix does not already deny.
- **(d) A wheel engine as an ordinary extra**: rejected. Beyond the same wall, it
  would ship green while being unusable on a supported-adjacent tier — a latent
  rule-10 violation, since the extras gate only runs on 3.13.
- **Declaring tesseract as a base *Python* dependency**: impossible; there is no
  engine on PyPI (`tesserocr` has no Windows wheels, `pytesseract` is a wrapper),
  which is precisely why decision 3 exists.

## Consequences

- A default install gains a working OCR path with one OS package; consumers that
  cannot install OS packages get a typed error naming what to install, and can
  opt into the pulled engine instead.
- The asset machinery is exercised by a **base** backend for the first time,
  which makes `models install/list` meaningful without any heavy extra.
- `pc-w3w` (first-use download notice) now covers a base path; the notice matters
  more, not less.
- Rule-10 scope is now explicitly stated (bead `pc-45p`) and the free-threaded
  tier is suspended with a recorded re-entry condition (ADR-0001 amendment); the
  extras-availability reporting job (`pc-7qq`) makes the gap observable without
  gating.
- Re-entry for a wheel-based default engine: when `onnxruntime` ships
  free-threaded macOS/Windows wheels, decisions 1 and 2 can be revisited — the
  adapter boundary is the backend, so the engine behind it can change.
