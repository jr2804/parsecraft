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
- `judge.py` — `RoutingJudge` Protocol + `DeterministicJudge`.
- `planner.py` — `plan_route` and its validation helpers.

## Local Contracts

- Hard constraints are code-owned and never delegated: extras installed,
  VRAM budget vs `requires_gpu`, source-format coverage, `allow_ocr`,
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
  widen.
- Feature hints are document-level diagnostics codes
  `feature:tables` / `feature:equations` / `feature:figures` (or
  `image_count` mass ≥ `HEAVY_IMAGE_COUNT`); backends/tests emit them.
- The judge is an injectable seam: `DeterministicJudge` is the default
  (preferred model → native before OCR → lowest VRAM → name). A Jev /
  System-One judge is a FUTURE optional extra implementing `RoutingJudge`;
  this package must never import Jev/System-One.
- Language detection is an injectable seam the same way:
  `language.LanguageDetector` (`detect_language(text) -> str | None`). The
  requested language arrives as plain data on `RoutingConstraints.language`
  and the core never imports a detector implementation — the ollaya/`laya`
  backed one lives in `parsecraft.providers.ollaya`, opt-in like a judge.
- Judge resolution lives in `judge_providers.py`:
  `resolve_judge(spec: str | RoutingJudge | None) -> RoutingJudge` —
  `None` → `DeterministicJudge`, instance → passthrough, else parse
  `provider/model[:variant]` (`parse_judge_spec` → `JudgeSpec` in
  `judge.py`) and dispatch. Loaders register via
  `register_judge_provider(name, loader)` (last wins; explicit beats the
  lazy path); unresolved providers lazy-load
  `parsecraft.providers.<provider>.load_judge(spec)` through
  `import_module` at resolve time only — never at module import, never
  inline (csort), never network. Typed errors: `JudgeSpecError` (malformed
  spec), `JudgeProviderUnavailableError` (names the provider extra to
  install), `JudgeProviderLoadError` (loader failed / non-judge return).
  Resolution only produces a judge — `plan_route`'s `_validate_order`
  remains the sole eligibility enforcement point.
- Determinism: backends sorted before use, signals sorted by page number,
  `primary` tie-break by name; no dict/set iteration leaks into output order.

## Verification

`mise test` — `tests/test_routing.py` (100% coverage gate applies; tests
build synthetic analyses from `tests/fixtures/sources.toml` expectations).
