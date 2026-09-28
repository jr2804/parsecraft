# ADR-0001: Phase 0 — name, licence, runtime floor, dependency profiles, config engine, acceptance targets

- **Status:** Accepted — name signed off by the user 2026-09-25; upstream host
  set to GitHub (private), superseding the Codeberg line below
- **Date:** 2026-09-25
- **Deciders:** ParseCraft maintainers
- **Related:** internal ParseCraft package plan (2026-09-25)

## Context

A prior internal pipeline ran Docling on a 113-page ITU-T P.863 PDF for >1 hour at
full CPU/RAM and was cancelled. This package must convert any document into typed
structured chunks (Markdown as a projection only), select the cheapest suitable
backend, escalate to OCR/VLM selectively, and stay within an 8 GB VRAM GPU budget.
The package is standalone; downstream consumers integrate through optional
adapters, never hard dependencies.

## Decisions

### 1. Distribution / import / CLI name: `parsecraft` (approved)

Verification evidence (2026-09-25, conclusive — no 503/anti-bot responses):

| Registry | Result |
|---|---|
| PyPI JSON `parsecraft`, `parse-craft`, `parse_craft`, `parsecraft-py` | all **404 = free** |
| GitHub user/org `parsecraft` | **404 = free** |
| Codeberg `jr2804/parsecraft` (user `jr2804` verified 200) | **404 = free**; repo-search empty |
| npm `parsecraft` | **404 = free** |
| crates.io `parsecraft` (with `User-Agent` header) | **404 = free** ("crate does not exist") |

**Conflict-check procedure (methodology, both traps hit during this research):**

1. PyPI: only the JSON API is authoritative. `pypi.org/project/<name>/` returns
   **HTTP 200 with a bot-challenge page** for non-existent names — never treat
   that as availability. A conclusive answer is `404` from
   `pypi.org/pypi/<name>/json`; `503`/challenge = inconclusive, re-query.
2. crates.io: returns **HTTP 403 without a `User-Agent` header**. A bare curl
   shows "forbidden", which looks inconclusive or taken. Always send a UA to get
   the real 404.
3. Check distribution name and import name separately; check hyphen/underscore
   variants; reserve the distribution only after the repo name is available.

Rejected alternatives with reasons:

- `docuforge`, `documind` — taken on PyPI (JSON 200).
- `ai-docs`, `ai-doc-parser`, `intelli-docs` — wave-locked naming; `intelli-docs`
  collides with an existing GitHub project; `ai-*` sits in Azure "AI Document
  Intelligence" vocabulary.
- `rubricate` — semantics excellent, spelling/pronunciation friction is a
  permanent support cost.
- `foliation` — ambiguous (bookbinding vs geology), weaker signal.
- `doc-intelligence-kit` — free but long; variants `doc-intel-kit`,
  `doc-intelligence` taken; awkward import/CLI.
- `structured-docs` — PyPI free but crowded (22 GitHub name hits incl. exact match).

Consequences of sign-off:

1. **Done 2026-09-25:** GitHub repo `github.com/jr2804/parsecraft` created; made
   public at release time (ADR-0002).
2. **Done 2026-09-27:** first release `2026.9.2` published to PyPI, reserving
   the distribution name (CalVer, not `0.0.1`).

**CLI:** exactly one binary, `parsecraft`. No abbreviation (`pc` collides,
`pcraft` unguessable); short-form ergonomics via documented shell alias if ever
needed.

**Entry-point group (frozen here):** `parsecraft.backends`.

### 2. Licence: MIT

**MIT.** Rationale: the config engine is deliberately self-contained and portable
so other projects can reuse it — a permissive licence keeps that frictionless.
Also matches the Copier template's default licence choice.

**Dependency rule:** every new dependency must be licence-compatible with MIT
(MIT/BSD/Apache-2.0/PSF/ISC acceptable without review; anything copyleft or with
additional restrictions requires an explicit Phase 0 addendum to this ADR before
it enters any profile).

### 3. Python runtime floor: `>=3.13`

- `requires-python = ">=3.13"` — aligns with the Copier template, which enforces
  `python_version >= 3.13` as a validator (its default answer is `3.13`).
- **CI matrix: 3.13 (GIL) + 3.14 (GIL) + 3.14t (free-threaded) + 3.15-dev
  (allowed to fail).**
- **Free-threaded policy:** 3.13 free-threaded (`3.13t`) is **not** supported;
  3.14+ free-threaded (`3.14t` and successors) **is** supported and CI-tested.
  `requires-python` cannot express a build-variant exclusion, so this is enforced
  by the CI matrix and documented here, not by package metadata.
- **3.14 status: supported, not aspirational** — the full core stack (pydantic
  2.13.5, pydantic-settings 2.15.0, platformdirs, tomlkit) ran live on CPython
  3.14.3 during this research. Free-threaded wheels exist for the Rust core dep:
  `pydantic-core` 2.49.0 ships `cp314t` wheels for Linux/macOS/Windows (verified
  2026-09-27 via the PyPI JSON API).
- GPU/OCR extras (`vllm`, `ocr-*`) may cap the upper bound when upstream wheels
  lag a new interpreter. Caps live in `pyproject.toml` (the OCR extras share a
  single `transformers` window), are documented, and are never applied to core.

### 4. Dependency profiles

Hard rule: **core is import-clean on an offline machine** — zero network
capability at import time; enforced by an offline import test.

| Profile | Contents |
|---|---|
| `core` | pydantic v2, pydantic-settings, platformdirs, tomlkit, charset-normalizer, typer (CLI), **markdown-it-py** (see §11). No HTTP. |
| `download` | `huggingface-hub` (all model-asset networking lives here) |
| `web` | URL fetching/extraction (`httpx`, `trafilatura`) |
| `pdf-lite` | `pymupdf` |
| `liteparse`, `pandoc` | adapters behind extras, pending dependency/licensing review |
| `ocr-ovis`, `ocr-tele`, `ocr-unlimited`, `ocr-qianfan` | one extra per OCR backend |
| `vllm` | GPU runtime extra |
| `auto-jev` | optional TypeSafe/Jev integration |
| `all` | intentionally heavy convenience profile |

Base install never pulls: model weights, CUDA, vLLM, SGLang, Docling, OCR
checkpoints.

### 5. Config engine: Route A (fresh)

Chosen stack: **pydantic-settings (per-layer source evaluation) + platformdirs +
tomlkit**. No off-the-shelf library satisfies the contract (evidence: confz legacy
pins / no provenance; dynaconf dict-magic; configobj INI; anyconfig no
validation/provenance) — feasibility of provenance via per-source diffing was
demonstrated live.

Requirements that make Route A the right choice (rather than reusing an existing
engine):

1. **Self-contained module boundary** — own package directory, zero imports from
   the rest of this package; splittable into its own distribution or vendorable
   by a consumer later.
2. **Zero document-specific assumptions** — no format, model, or pipeline
   concepts in the config layer.
3. **Generic naming** — `ConfigEngine`, `ConfigLayer`, `ConfigSource`.
4. **Behavioral reference spec = a predecessor config engine** — tests pin the
   observed behavior it got right:
   - layer order: `default → global → project → env → cli`
   - per-key provenance records + `source_of()`
   - deprecated-alias migration with warnings
   - `config check` report shape
   - `${VAR}` secret references (never literals in example files)
5. **Contract requirements beyond the reference behavior (inherit lessons, not bugs):**
   - platform-appropriate global config dir via **platformdirs** (a hardcoded
     XDG-only path fails on Windows/macOS)
   - **declaring-file-relative** path resolution (CWD-relative resolution is wrong)
   - deterministic effective-configuration snapshots for benchmarks
   - deprecated-key migration writes via **tomlkit** (style-preserving)

### 6. Acceptance targets (falsifiable, fixed before Phase 2 measurement)

| Metric | Target |
|---|---|
| Full 113-page P.863, auto-mode, **cold cache** (headline) | ≤ 15 min wall time (vs >60 min Docling unfinished) |
| Warm-cache run | reported separately, not the headline |
| Fast-path page latency | p95 ≤ 20 s |
| Any single page (hard ceiling, catches stalls) | ≤ 60 s or structured failure |
| Peak VRAM | ≤ 7.5 GB of 8 GB (deliberate headroom) |
| OCR escalation rate | reported (fraction of pages needing a visual pass) |
| Dropped pages | zero, silently or otherwise |

Measurement conditions (batch size, page resolution, hardware profile) recorded
in every report; goalposts not movable after Phase 2 begins.

### 7. Pass budget policy

Every pass — native or OCR — runs under a deterministic time budget. On breach:
record a structured failure and escalate to the OCR route **for that page range
only**. Docling may never consume the whole document's budget. "Native extraction
before OCR" is a rule, not a suggestion.

**"Structured failure" is a typed record, not a string:** a pydantic model in the
IR (working name `PassFailure`) carrying at minimum: failure code (enum), pass
kind, page range, backend name + version, budget seconds, elapsed seconds,
detail, timestamp. The processing trace holds these records; benchmark reports
aggregate them. Final field set lands with the IR design in Phase 0 implementation.

### 8. Fixtures policy

ITU-T P.863 is copyrighted: **no document bytes committed** (no PDFs or other
fixture binaries in git). Two tiers:

1. **Synthetic fixtures, scaffolded at test time** into the pytest cache
   (`tests/test-cache/`, via `request.config.cache`): tiny generated
   `.txt/.md/.csv/.json/.html` and a minimal valid PDF built from bytes in test
   code. The generator is the committed artifact, never the binary.
2. **Real-document fixtures, opt-in and offline-by-default**: a manifest
   (`tests/fixtures/sources.toml`) records public sources with URLs + the
   documented local download step. Fetches are skipped unless explicitly
   enabled; `mise test` never needs network. Downloads live in a project-owned,
   gitignored `tests/downloads/` staging dir — never in the pytest cache, which
   pytest owns. Two sub-classes:
   - **Pinned** (`sha256` + `approx_size`): reproducible benchmark inputs; the
     cache is content-addressed (`sha256-filename`) and a hash mismatch fails
     loudly as drift, to be re-pinned deliberately.
   - **Mutable** (`mutable = true`): live wiki/spec/archive pages that
     legitimately change upstream; cached by filename and validated structurally
     (format plausibility), never by hash, so an upstream edit never fails the
     tier.

Core stays import-clean offline (enforced by `tests/test_offline_import.py`).

**Corpus requirements** (Phase 2 harness, reused by auto-mode in Phase 4):

- **Complexity spread** — simple/short through highly complex/long documents.
- **Feature coverage** — images/figures, equations, tables, and code segments,
  at varying density, recorded per document.
- **Time budget** — the default `mise test` stays fast and offline; a separate
  opt-in corpus run may take up to **15 minutes on a cold cache** and MUST cache
  downloads and results (content-addressed, keyed by URL + checksum) so later
  runs are fast.
- **Routing harness** — per-document expectations (complexity tier, expected
  features/signals) live as manifest *data*, so the same suite can later drive
  auto-mode and optional Jev judgments. Expectations are data, not test logic.

### 9. Schema layer

pydantic v2 is a hard project standard: concrete types only, no `Any`, no bare
`dict` duck-typing. Applies to IR, config, backend protocol.

### 10. `py.typed` is mandatory in the scaffold

Type rigor is a hard project standard, and a package that ships no `py.typed`
does not export it — downstream type checkers see `Any` everywhere and the
standard evaporates at the distribution boundary.

- `py.typed` marker in the package, included in the wheel.
- **Packaging test:** build the wheel, assert `py.typed` is present in its
  contents, and type-check a scratch consumer that imports the installed wheel.
- Note: the Copier template does **not** currently ship `py.typed` (verified
  against the template tree) — we add it explicitly; candidate for an upstream
  template fix.

### 11. Markdown renderer: split decision, both halves named now

1. **IR → Markdown projection: in-package deterministic renderer, zero
   dependencies.** Our output is the contract downstream systems consume (wiki
   pipelines, benchmark diffs); owning the renderer byte-for-byte is the only way
   "deterministic projection" is falsifiable. No third-party renderer can define
   our contract.
2. **Markdown input (`.md` files) → IR: `markdown-it-py`** (4.2.0, MIT,
   CommonMark-compliant, pure Python, active — released 2026-05). Rejected:
   `marko` (2.2.4, no licence classifier on PyPI, smaller ecosystem),
   `markdown`/Python-Markdown (3.11, not CommonMark — dialect drift would make
   round-trips non-deterministic).
3. **`markitdown` is explicitly NOT adopted or recommended** as a
   document→Markdown conversion backend. It is fast and covers many office
   formats, but produces acceptable results only with cloud-based (paid)
   extensions; until it is measured to outperform LiteParse on this project's
   fixtures, it must not be suggested or added. (`markdown-it-py` above is a
   CommonMark parser, unrelated to `markitdown`.)

### 12. Explicitly deferred (not in v1 acceptance)

- email and archive input classes ("where applicable" undefined in plan)

## Scaffold record

- Real Copier template `https://codeberg.org/jr2804/copier-uv-plus.git` at
  template commit `2026.09.7`; all answers recorded in `.copier-answers.yml`.
- Template URL **must end in `.git`**: copier's `get_repo()` only recognizes
  remote VCS URLs ending in `.git` (or github/gitlab prefixes); the bare URL
  from the plan is silently treated as a local path and fails with
  "Local template must be a directory."
- Copier requires `--trust` (template uses `jinja_extensions` + `tasks`); the
  template's auto-created initial commit was removed to honor the project's
  manual-commit rule (files untouched, git re-initialized, remote preserved).

## Consequences

- Name is reserved by first publish; all public actions gated on user sign-off.
- The dependency licence rule keeps downstream adoption legally clean.
- The config layer is portable and self-contained, so it can be reused or
  vendored without coupling.
- Acceptance criteria are falsifiable; Phase 2 benchmark harness must implement
  the measurement conditions above verbatim.
- `PassFailure` typed record is an IR deliverable, not an afterthought.

## Non-goals for Phase 0

No model adapters, no asset manager, no hardware profiler, no scaffold or
directory creation until the user signs off on the name.
