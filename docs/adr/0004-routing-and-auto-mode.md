# ADR-0004: Routing and auto mode — deterministic analysis first, optional judge second

- **Status:** Accepted
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
`DeterministicJudge` (preferred model first, native before OCR on `NATIVE`,
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
