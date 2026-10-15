# ADR-0004: Routing and auto mode — deterministic analysis first, optional judge second

- **Status:** Accepted · Amended 2026-10-01 (classifier seam, below)
- **Date:** 2026-09-27
- **Deciders:** pc-1, pc-2, pc-3
- **Related:** ADR-0001 §6 (acceptance targets), §7 (pass budget), ADR-0003
  (optional AGPL extra), bead `pc-5ub`, `docs/architecture/routing.md`

## Context

ParseCraft aggregates backends with very different costs: the native family is
dependency-light and offline; external converters carry heavy dependency graphs;
OCR/document-VLM backends need a GPU, pinned weights, and an explicit extra. A
full document should not run the most expensive route by default, and the
motivating incident (ADR-0001) is a converter that consumed >1 h on a 113-page
PDF without finishing.

`analyze()` already produces deterministic per-page signals — native text
present, text characters, replacement-character ratio, image count, blank pages.
Those signals are enough to route most pages without a model. The open question
is how to add a smarter, optional decision layer without letting it own safety,
cost, or licensing constraints.

## Decisions

### 1. Deterministic analysis is the mandatory first step

`plan_route(analysis, backends, constraints, judge=None) -> RoutingPlan` is a
pure function in `parsecraft.routing`: same inputs produce a byte-identical
plan. It never fetches, never imports a heavy stack, and never raises on a page
it can route.

### 2. Per-page intent comes from a single rule table

Each page is classified into an `Intent` (`NATIVE`, `OCR_GENERAL`, `OCR_TABLES`,
`OCR_VISION`) by one rule table in `routing/rules.py`, not by scattered
conditionals. The thresholds (`NATIVE_MIN_TEXT_CHARS`, `MAX_REPLACEMENT_RATIO`,
`HEAVY_IMAGE_COUNT`) are constants in that module.

### 3. Eligibility is code-owned and non-negotiable

A candidate backend is excluded from the plan unless it satisfies every hard
constraint in `RoutingConstraints`: installed extras, VRAM budget, format
coverage, `allow_ocr`, and offline/model-asset rules. The intent family is also
code-owned — OCR intents accept only OCR backends, while `NATIVE` leads with
native backends and uses OCR only as fallbacks, and requires at least one
eligible non-OCR backend. A judge never sees an ineligible candidate.

### 4. The judge is an optional, bounded seam — not a decision authority

`RoutingJudge` is a `runtime_checkable Protocol` with one method:

```python
def rank(self, intent: Intent, candidates: Sequence[BackendDescriptor]) -> Sequence[str]
```

It receives only eligible candidates and returns backend names, best first. It
may re-rank or drop candidates; it may not add one. The default is
`DeterministicJudge` (preferred model first; on `NATIVE` the dependency-free
native family leads, extra-backed parsers follow, OCR last,
then lowest estimated VRAM, then name). When `judge=None`, the deterministic
ordering is used, so routing works with no judge installed.

### 5. The judge cannot override a constraint

A judge that returns an ineligible name, a duplicate, or an empty order raises
`JudgeViolationError`. Missing candidates for an intent raise
`NoEligibleBackendError`; empty analysis raises `RoutingError`. There is no
silent fallback that ignores a violation.

### 6. Fallbacks are extra passes, not failure records

`max_passes` caps `PageRoute.candidates`, where `candidates[0]` is the pass-1
route and the remainder are ordered fallbacks. Routing does not record
execution failures — those are `PassFailure` records on the backend result.

### 7. Jev / System One is a future judge adapter, not a dependency

The judge seam is the integration point for an optional System One / Jev-backed
judge. No such adapter exists in the package; it would ship as its own optional
extra and remain subject to decisions 3–5.

## Consequences

- Routing is deterministic and unit-testable end to end; the plan is a pure
  function of analysis plus descriptors plus constraints.
- A smarter decision layer can be added or removed without touching the rules or
  the backends.
- OCR stays selective and budgeted: it can only be chosen where signals show
  native text is missing or unusable.
- No judge, no GPU, and no network are required to route a document.
- Per-range (rather than per-page) assignment and failure records inside the
  plan are explicitly out of scope for this decision.

## Alternatives considered

- **Single-shot LLM routing over the whole catalog.** Rejected: nondeterministic,
  cannot own VRAM/licence safety, and unavailable offline.
- **Hard-coded cheapest-first heuristics in the conversion path.** Rejected:
  rules would spread across call sites, with no single table to test or extend.
- **A third-party routing library.** Rejected: no provenance-carrying typed
  output, and it would add a dependency the core does not need.

## Amendment 2026-10-01: optional OCR-need classifier seam

- **Status:** Accepted
- **Date:** 2026-10-01
- **Deciders:** pc-1
- **Related:** gh-3, beads `pc-rzm`/`pc-1ow`, plan doc
  `.agents/plans/pdf-pre-router/00-recon.md` (gitignored), backend ADR-0004
  decisions 1–7 (all unchanged)

Motivation: `pdf-inspector` (MIT, Rust/PyO3, prebuilt abi3 wheels, zero Python
dependencies) detects per-page OCR need from the PDF text layer in tens of
milliseconds — strictly better evidence than the text-length proxy of decision
2, at negligible cost, running in `analyze()` before any conversion is chosen.
Decisions 1–7 are untouched; this adds a second optional seam beside the judge.

### A1. The classifier is a signal source, not a decision authority

`PageOcrClassifier` (Protocol, `routing/classifier.py`) produces `OcrFacts` —
per-page OCR-need and table pages, plus document `pdf_type`/`confidence`. A
pure fold (`apply_classifier` in `pipeline/analysis.py`) merges facts into the
`AnalysisResult` at the analysis boundary; `plan_route` keeps its signature and
stays pure (decision 1). The classifier cannot widen eligibility, override
`allow_ocr`/VRAM/offline/licence constraints, or route a native-intent page
away from native fallbacks — the code-owned funnel of decision 3 remains the
only authority.

### A2. Augment-only: the classifier may add OCR-need, never remove it

OCR-need = `page_needs_ocr(signal) OR (signal.classifier_needs_ocr is True)`.
The field is `True`-only semantics: `False` is never a verdict. Text-statistics
OCR-need (blank/thin/garbled) means native extraction genuinely fails, so a
`text_based` verdict must never force NATIVE on such a page. Over-routing to
OCR costs money; under-routing loses content — correctness outranks cost.
Document-level `pdf_type` never overrides a per-page verdict; it only refines
OCR flavor.

### A3. Fallback is the rule table, never the judge

With no classifier (or on classifier error), pages are classified exactly as
today by the decision-2 rule table; absence of a classifier IS the current
behavior — there is no new fallback path. The judge keeps its decision 4–5
role (re-rank only) and is never a classifier fallback.

### A4. Implementation contract: local-only, model-free, deterministic

Implementations MUST be a structural text-layer scan (tens of milliseconds):
no network, no model downloads, and no model loads at all — an ML-model
classifier deciding whether to run an ML backend is oversized and out of
contract. Facts page numbers are IR 1-based by contract (upstream indexing is
inconsistent: `classify_pdf`/`PageMarkdown.page` 0-indexed, `detect_pdf`
1-indexed); the provider normalizes once, nothing downstream converts.
`detect_pdf` is the chosen upstream call (1-indexed like the IR; carries
OCR reasons and table pages). `resolve_classifier(spec | instance | None)`
resolves `None → None` — there is NO default implementation, unlike the
judge's `DeterministicJudge`.

### A5. Provenance via diagnostic, not schema change

The fold emits one document-level diagnostic carrying classifier name, upstream
version, `pdf_type`, and confidence — enough to reconstruct why plans differ
across environments. No `DocumentResult`/trace schema change. Determinism
statement: identical inputs plus identical installed classifier version yield
identical plans. The only contract change is one generic nullable field,
`PageSignal.classifier_needs_ocr: bool | None = None` (default `None` —
existing constructors unaffected).

### A6. Table hints flow through the existing channel

The fold emits `feature:tables` when the classifier reports table pages,
filling the decision-2 hint channel with real evidence; `feature:figures` is
never synthesized (no honest upstream signal). Hints only select OCR flavor
for pages already needing OCR, so A2's asymmetry holds.

### A7. System One / Jev: same pattern, own extra, no heuristic by default

Decision 7 stands: the judge seam is the LLM integration point for
re-ranking. The classifier seam extends the same pattern to OCR-need facts;
a Jev-backed judge OR classifier would ship as its own optional extra,
subject to A1–A4. The requirement "a no-heuristic LLM path must be
configurable" is met by spec-string resolution (`provider/model[:variant]`)
on both seams; the heuristic path remains the default when no spec is given.

### A8. The judge ranks with page context — authority refinement, not a boundary move (2026-10-02 amendment)

User decision: the System One judge is the PRIMARY ranking driver; the
deterministic judge remains the no-spec fallback. This refines d3–d5 without
moving any rail:

- For OCR-needy pages the judge state MAY carry bounded page context: the
  rule-table flavor label (`OCR_TABLES`/`OCR_VISION`/`OCR_GENERAL`), the
  document-level `FeatureHints`, and class-level page facts (blank,
  needs-OCR) — in addition to intent, candidate capabilities, machine,
  preference. Still no document content, still bounded (d5).
- The judge's ordering stays fully authoritative WITHIN the family. The code
  rails are untouched: OCR-need is code/classifier-owned and augment-only
  (A2), a NATIVE page is never led by OCR (`_validate_native_fallbacks`),
  eligibility is never delegated (d3).
- Flavor-specialized candidates MAY declare OCR flavor capabilities; the
  planner uses declarations to narrow or label — never to admit what the
  rules rejected.
- Memoization contract: per-page-variable facts must not enter the judge
  state without entering the memo key; document-level facts (hints, machine,
  preference) are constant per plan and stay out of it (machine/preference
  precedent).

> Superseded for the "no context for NATIVE" half by the A9 pc-86l widening
> below: every intent now carries a page context.

### Alternatives considered (classifier attachment)

- **Enrich `pymupdf.analyze()` with the classifier.** Rejected: the same
  bytes would produce different canonical signals depending on the installed
  environment, and it couples the light pypdf path to a second PDF library.
- **A classifier-only `DocumentBackend`.** Rejected: `choose_analyzer` ranks
  native backends first, so it could never win PDF analysis; a converter it
  does not need would be mandatory protocol surface.
- **Full contract change (`pdf_type`/confidence on `PageSignal`,
  `AnalysisResult.classification`).** Deferred: widens the frozen public
  contract and every consumer for data only diagnostics need; revisit only if
  provenance must be machine-read.

## Amendment (2026-10-09): offline excludes downloads, not assets

**Decision (user ruling, bead `pc-m0k`):** `offline` means *do not download* —
literally. A model-asset backend stays eligible offline when its weights are
**verified present** in the managed cache, and is excluded only when they are
not proven present.

**Predicate.** `is_hard_eligible` admits a backend carrying `model_asset` under
`constraints.offline` iff its `model_id@revision` appears in the new
`RoutingConstraints.cached_assets` fact. One condition, no new routing
primitive, no backend name special-cased; `allow_ocr`, engine, GPU and format
rules are untouched, and online behaviour is unchanged.

**What "verified present" means.** `AssetManager.is_verified(pin)` reuses the
verification-marker semantics introduced for the re-hash fix (`pc-u4q`): the
marker's recorded digest equals the pin's expected digest and the file's size
and `st_mtime_ns` are unchanged. It is a pure cache read — no download, no
re-hash. Missing, foreign, damaged, other-revision or half-written markers count
as **absent**, as do assets with no per-file manifest, so the failure direction
is fail-safe: anything not proven present is treated as a download that would be
needed.

**Probe economy.** `EnvironmentInfo.cached_assets` is computed only for assets
that some registered backend actually declares, and its `AssetManager` carries a
never-downloads downloader, so verification can neither fetch nor import the Hub.

**Second line of defence unchanged.** The convert-time pre-attempt refusal
(`options["offline"]` → typed `DEPENDENCY_MISSING`) remains for the genuinely
absent case; this amendment changes *eligibility*, not the refusal.

**Why the rule was wrong as written.** `RoutingConstraints.offline` defaults to
`True` for embedders (network is opt-in). Excluding *assets* rather than
*downloads* therefore made that default silently exclude MinerU and the four OCR
VLM backends on a host whose multi-GB weights were already sitting in the cache —
an ineligibility with no network consequence at all, which reads from the outside
as a routing bug. Weights verified in the managed cache are not a download.

**Consequence to expect.** With a warm verified cache an offline-declared plan
may now lead with MinerU or an OCR VLM backend; clear the cache and they are
excluded again — which is the behaviour the rule was always meant to express.

## Amendment (2026-10-12): A10 — the NATIVE tier is host-fact aware

**Decision (user ruling, shape B, bead `pc-9tr`):** on `Intent.NATIVE`,
dependency-free means dependency-free **for this host** — both `analyze()` AND
`convert()` must work without an extra on this host. A backend whose
`convert_extra` is absent on the host is treated as extra-backed for that host:
**demoted to tier 1, never hidden.** The candidate set is still complete (no
backend is dropped from candidacy), but the lead is honest.

**Why a new field.** `optional_dependency_group` is ambiguous about *which
path* needs the group (knox-1's structural finding): `pymupdf` declares no
group because its analysis is core, yet its convert path crosses
`parsecraft[pdf]`/PyMuPDF. The new `BackendCapabilities.convert_extra` records
exactly the convert path's import boundary — **not every import**;

**Cross-reference (2026-10-14):** the AGPL extra was renamed `pdf` →
`pymupdf` (package-named convention; see ADR-0003 amendment). Consumers
of `parsecraft[pdf]` must switch to `parsecraft[pymupdf]`.
analyze()-only imports never appear there. `None` (the default) means convert
needs nothing beyond core/base dependencies.

**Mechanism.** `DeterministicJudge` carries `installed_extras` (default empty —
the honest shape for a caller with no host facts); `plan_route` passes
`constraints.installed_extras` to the judge it builds. In `_sort_key`'s NATIVE
branch, a candidate whose `convert_extra` is set and absent from that set takes
tier 1 alongside the declared extra-backed parsers (both sort by the existing
VRAM proxy → name). `is_hard_eligible` is untouched: eligibility never changes,
only ranking. `max_passes` semantics are unchanged; with `max_passes=1` on a
host without `[pdf]` the lead is simply the first installed extra-backed
parser, so knox-1's wasted failed lead attempt is gone.

**Descriptor mapping (scored by System One/Jev, verified against source;
bead pc-9tr).** `native-text`/`-markdown`/`-html` and `ocr-tesseract` (engine-
gated, not import-gated): `None`. `pymupdf` → `pdf`. `docling` → `docling`,
`liteparse` → `liteparse`, `marker` → `marker` (bring-your-own group, ADR-0006),
`mineru` → `mineru`, `pandoc` → `pandoc`, `pdf-inspector` → `auto`,
`trafilatura` → `web`, and the four OCR VLM backends (`ovis`, `tele`,
`unlimited`, `qianfan`) → `ocr`.

**installed_extras surface (System One/Jev consult, bead pc-9tr).** The
existing import-surface probe (`EXTRA_IMPORTS` + `find_spec`, all-of markers)
is **direct and sufficient** — no transitive closure over extras: detection
keys on the package import itself, so a package arriving transitively still
flips its own extra's detector. Known corner: `auto` requires both
`pdf_inspector` and `typesafe_sdk` (all-of), so a hand-installed
`pdf_inspector` alone leaves `pdf-inspector` demoted — over-demotion is the
safe direction under this amendment (tier 1, still a candidate).

**Provider judges unchanged.** A resolved provider judge (System One, Zen,
Ollama) re-ranks the same complete candidate set with the host facts A8 already
carries; `_validate_native_fallbacks` still enforces native-before-OCR on its
orders.

## Amendment (2026-10-12): A9 — the judge receives a normalized density fact

**Decision (user ruling, shape B "trust the model", bead `pc-bzz`; design
lineage gh#6/knox-1):** the A8 page context grows a `density` field — a
normalized `float` in `[0.0, 1.0]`, `text_chars` over the new
`DENSITY_REFERENCE_CHARS` (1000, deliberately separate from
`NATIVE_MIN_TEXT_CHARS = 40` so the classification's ≥-chars binary is
untouched), saturated. **No bucket: the bucket is the model's call.**
Classification is unchanged. The reason prose stays audit-only — it gains no
`density=` field; the typed fact is what the model reads.

**Memo key.** `memo_key()` grows the field: `(needs_ocr, blank, density)`, so
a memoized judge never shares a verdict between two pages that differ only by
density (the knox-1 corpus spans a 17× range, 121–6066 chars/page — the
resolution the ≥40 binary used to throw away).

**Scope (widened 2026-10-12, pc-86l — user ruling (b) "expand now").** The
original landing rode the existing A8 gate: only OCR-intent pages carried a
context. That scope was narrower than gh#6's evidence — the corpus table is
**all-NATIVE** (sparse 121–623 chars/page → docling, dense 4894–6200 →
mineru), so the very pages the issue measured got no context at all.
`page_context()` now returns a context for **every** intent: NATIVE pages
carry `needs_ocr=False` plus the same `blank`/`hints`/`density`. A8's
"no context for NATIVE" rule is superseded for the page-class fact; the
deterministic fallback judge still ignores the context (a pure function of
family and preference), and `plan_route`'s NATIVE-lead guard still enforces
native-before-OCR. The wire state (`providers/_jev.py` `request_state`) sends
`page{needs_ocr, blank, density}` for both intents, and the `instructions()`
prose names `density` so the prompt and the state agree. The field defaults
to `0.0` — the honest "no text signal" for a context constructed without a
page behind it.

> **Amended 2026-10-15 (pc-4u7.41):** the NATIVE-lead rule is a *tier cap*, not a
> prohibition. `extraction_tier` (rules.py) grades each backend 0 (native) → 1
> (intelligent parser) → 2 (OCR/document-VLM); a judge may not lead above the cap
> when the caller named no backend, and naming one (`preferred_backend`) raises
> the cap to that backend's tier. "Native before OCR" therefore survives as the
> *default*, while the VLM harness stays reachable for digital PDFs when the
> caller asks for it. `_sort_key`'s host-aware ranking (A10) is unchanged.

**Released-artifact scope (verified 2026-10-13).** This widening is on `main`
**only**: the `2026.10.3` tag still carries the old rule —
`page_context()` there returns `None` for every NATIVE intent and contains no
`pc-86l` reference — so a consumer importing the released wheel sees the
pre-widening behaviour until the next release is cut from a tip that contains
this commit. Verified with `git show 2026.10.3:src/parsecraft/routing/judge.py`,
not from the changelog. Recorded here because "the issue is fixed" and "your
installed wheel has the fix" are different claims, and the A9/A8 notes around
this one read as if they were the same; the distinction cost a downstream
consumer (gh#6/knox-1) a correction in each direction.

**Encoding consult (System One/Jev, bead pc-bzz).** Asked whether a float or a
delivered `sparse/medium/dense` label is the better A8 fact, the model
preferred the label but with no extractable confidence — the dispatch's
low-confidence branch. Shipped the float (also the semantically right call:
a delivered label would re-introduce exactly the pre-bucketing the shape-B
ruling rejected). A future ruling can add a label at the provider layer
without touching this contract. Reference value 1000 endorsed by the same
consult.
