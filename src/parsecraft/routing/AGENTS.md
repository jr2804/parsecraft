# AGENTS.md — src/parsecraft/routing/

Auto-mode routing harness: deterministic signal → intent → plan with a
judge seam.

## Purpose

`plan_route(analysis, backends, constraints, judge=None) -> RoutingPlan`
turns `AnalysisResult` page signals into a per-page route: an ordered
candidate list (≤ `max_passes`), the chosen backend, and a deterministic
reason. Pure function — no network, no heavy imports, identical inputs →
identical plan.

## Ownership

- `models.py` — `Intent`, `RoutingConstraints`, `PageRoute`, `RoutingPlan`,
  `RoutingError` hierarchy.
- `rules.py` — the single signal→intent rule table (`INTENT_RULES`,
  thresholds `NATIVE_MIN_TEXT_CHARS` / `MAX_REPLACEMENT_RATIO` /
  `HEAVY_IMAGE_COUNT`), feature-hint extraction, hard eligibility.
- `judge.py` — `RoutingJudge` Protocol + `DeterministicJudge` + `JudgeSpec`,
  `MachineProfile` (the host facts a judge may rank with), and the
  `JudgeProviderLoader` protocol (spec + optional host facts → judge).
- `classifier.py` — the optional OCR-need classifier seam (`PageOcrClassifier`
  Protocol, `OcrFacts`, `parse`/`register`/`resolve_classifier`); see
  ADR-0004 A1-A7.
- `planner.py` — `plan_route` and its validation helpers.

## Local Contracts

- Hard constraints are code-owned and never delegated: extras installed,
  GPU usability *and* VRAM budget vs `gpu_requirement` (only
  `GPU_REQUIRED` = 1.0 is hard-gated, and only with a usable runtime and a
  fitting declared estimate), source-format coverage, `allow_ocr`,
  `offline` (excludes model-asset carriers), and `language` (a BCP-47
  request only narrows backends that *declare* `capabilities.languages` —
  language-agnostic candidates such as the native family are never
  excluded). Plus code-owned intent family:
  OCR intents accept OCR backends only; NATIVE admits native + OCR
  fallbacks but requires at least one eligible non-OCR backend — otherwise
  `plan_route` raises `NoEligibleBackendError(intent=NATIVE, "no native
  backend covers the source format")`, never silently routing a
  native-intent page to OCR. A judge returning an ineligible/duplicate/
  empty candidate raises `JudgeViolationError` — it can re-rank, never
  widen. **Ordering is contractual too**: for a NATIVE page the order must
  keep every OCR candidate behind every native-capable one (they are
  admitted as fallbacks), so `_validate_native_fallbacks` rejects an order
  that promotes one — closing the hole where a provider judge could spend
  minutes on a VLM for text a native backend reads in milliseconds.
- Feature hints are document-level diagnostics codes
  `feature:tables` / `feature:equations` / `feature:figures` (or
  `image_count` mass ≥ `HEAVY_IMAGE_COUNT`); backends/tests emit them.
- The judge is an injectable seam: `DeterministicJudge` is the default
  (preferred model → native before OCR → VRAM direction → name). Shipped judge
  providers live in `parsecraft.providers` (the three System One endpoints
  `typesafe-ai`/`zen`/`ollama` sharing `providers/_jev.py`); this
  package must never import one.
- `rank(intent, candidates, context=None)` is the seam's call shape (A8).
  `context` is a `PageContext` — bounded, class-level page facts (`needs_ocr`,
  `blank`) plus the document-level `FeatureHints` — and it is built by
  `page_context(...)` for an OCR intent only; a native page (including one
  degraded to native) passes `None`, so native ranking sees exactly what it saw
  before. `PageContext.memo_key()` is the contract that keeps a judge's memo
  sound: whatever enters the state as a per-page fact enters the key, while
  plan-constant facts (`hints`, `machine`, `preference`) stay out of it.
  `DeterministicJudge` ignores the context on purpose — the fallback must stay a
  pure function of family and preference.
- `RoutingConstraints.preference` (`RoutingPreference`, default `BALANCED`) is a
  ranking axis, never a permission: it reorders candidates inside the family the
  rules already picked and never moves one across a family boundary. The judge
  the planner builds itself honours it (`DeterministicJudge(constraints.preference)`);
  an injected judge carries its own policy, resolved by the caller with the same
  value. `QUALITY` flips the VRAM direction — a **documented proxy** for model
  strength, not a measurement (an undeclared size sorts last either way).
- Degradation is the mirror of the NATIVE-lead guard: when an OCR-intent
  page finds no OCR family (missing extras / `allow_ocr` off / no usable GPU)
  but native can tell the truth about it, `plan_route` degrades that page to
  NATIVE with a recorded reason ("OCR unavailable … degraded to native …")
  instead of failing a valid document. Two shapes may degrade: a page with
  native text, and a **blank** page (pc-ztq — nothing to read, so the empty
  native page is the honest result; refusing there once failed whole documents
  over a page with no content). A page that visibly holds content but has no
  text layer keeps raising `NoEligibleBackendError`: native would silently emit
  an empty page for a scan. Rule lives in `rules.can_degrade_to_native`;
  classification itself is unchanged, so blank pages still route to OCR
  whenever an OCR backend is eligible. Codes: `degraded-garbled-text`,
  `degraded-short-text`, `degraded-blank-page` (score 0.0).
- Language detection is an injectable seam the same way:
  `language.LanguageDetector` (`detect_language(text) -> str | None`). The
  requested language arrives as plain data on `RoutingConstraints.language`
  and the core never imports a detector implementation — none ships; callers
  implement the protocol or set the constraint directly.
- The OCR-need classifier is a second injectable seam
  (`classifier.PageOcrClassifier` → `OcrFacts`; `resolve_classifier(None)` is
  `None` — there is NO default impl, unlike the judge). Facts are augment-only:
  `page_needs_ocr` ORs `signal.classifier_needs_ocr is True`, so a verdict can
  add OCR-need, never remove it. Implementations must be local-only,
  model-free structural scans; the core never imports one. `OcrFacts.source`
  MUST carry the classifier name and upstream version (e.g.
  `pdf-inspector 1.25.2`) — the fold records it as provenance. The fold lives
  in `pipeline.analysis.apply_classifier`; `plan_route` is untouched. See
  ADR-0004 A1-A7.
- Judge resolution lives in `judge_providers.py`:
  `resolve_judge(spec: str | RoutingJudge | None, *, machine: MachineProfile |
  None = None, preference: RoutingPreference = RoutingPreference.BALANCED) -> RoutingJudge` —
  `None` → `DeterministicJudge`, instance → passthrough, else parse
  `provider/model[:variant]` (`parse_judge_spec` → `JudgeSpec` in
  `judge.py`) and dispatch. Loaders register via
  `register_judge_provider(name, loader)` (last wins; explicit beats the
  lazy path); unresolved providers lazy-load
  `parsecraft.providers.<provider>.load_judge(spec, machine, preference)` through
  `import_module` at resolve time only — never at module import, never
  inline (pyreorder), never network. Typed errors: `JudgeSpecError` (malformed
  spec), `JudgeProviderUnavailableError` (names the provider extra to
  install), `JudgeProviderLoadError` (loader failed / non-judge return).
  Resolution only produces a judge — `plan_route`'s `_validate_order`
  remains the sole eligibility enforcement point.
- Determinism: backends sorted before use, signals sorted by page number,
  `primary` tie-break by name; no dict/set iteration leaks into output order.

## Verification

`mise test` — `tests/test_routing.py` (100% coverage gate applies; tests
build synthetic analyses from `tests/fixtures/sources.toml` expectations).
