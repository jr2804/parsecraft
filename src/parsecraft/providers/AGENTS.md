# AGENTS.md — src/parsecraft/providers/

## Purpose

Provider plug-ins for the two optional routing seams: judge providers resolved
by `routing/judge_providers.py` (each turns a `JudgeSpec` into a
`RoutingJudge`) and OCR-need classifier providers resolved by
`routing/classifier.py` (each turns a `ClassifierSpec` into a
`PageOcrClassifier`).

## Ownership

- `ollaya.py` — the ollaya daemon provider (`load_judge`, the verified wire
  format, `OllayaJudge`, `OllayaJudgeError`, plus the opt-in
  `OllayaLanguageDetector` / `load_language_detector` implementing
  `routing.language.LanguageDetector`).
- `pdfinspector.py` — the pdf-inspector classifier provider
  (`load_classifier`, `PdfInspectorClassifier`, the pinned detection call and
  its 1-based field semantics). Needs the `pdf-inspector` extra (shared with
  the backend family of the same name).
- `systemone.py` — the TypeSafe System One / Jev judge provider (`load_judge`,
  `JevJudge`, `JevJudgeError`): one Choice question per page intent over the
  eligible candidates, ranked by the choice distribution. Needs the `systemone`
  extra (`typesafe-sdk`, surface verified against 0.7.2) and
  `TYPESAFE_API_KEY`.

## Local Contracts

- Every provider module exports its seam loader — `load_judge(spec:
  JudgeSpec) -> RoutingJudge` or `load_classifier(spec: ClassifierSpec) ->
  PageOcrClassifier` — and is imported lazily (via `importlib`) on first
  resolve: top-level imports stay light (stdlib/core only); network, heavy
  work, and the optional-extra import happen in the loader or at call time,
  never at module import and never inline (pyreorder hoists inline imports).
- A judge only re-ranks candidates `plan_route` already deemed eligible —
  never widen, duplicate, or invent names.
- A classifier is a signal source, not an authority: it returns IR-1-based
  `OcrFacts` (page numbers >= 1; the provider normalizes upstream indexing
  once, nothing downstream converts) and can only ADD OCR-need (ADR-0004
  A1-A2). It must be a local, model-free structural scan — no network, no
  model downloads, **no model loads** — so a provider calls the upstream
  detection path only, never an OCR or extraction entry point.
- `OcrFacts.source` carries the classifier name and upstream version (e.g.
  `pdf-inspector 1.25.2`); the analysis fold records it as provenance.
- Every expected failure leaves the seam as a typed error: `ClassifierError`
  subclasses for classifiers (`analyze_source` falls back to the rule table
  for those only — anything else propagates), judge errors for judges. A
  missing optional extra surfaces as `ClassifierProviderUnavailableError` /
  `JudgeProviderUnavailableError` naming the extra to install, never as a
  silent `None`.
- A judge credential is resolved once at `load_judge`, never mid-conversion:
  an unset `TYPESAFE_API_KEY` (or a missing `systemone` extra) raises
  `JudgeProviderUnavailableError`, so the CLI reports `judge unavailable: …`
  before any backend runs.
- Daemon/config reading happens at call time (`OLLAYA_BASE_URL`), never at
  import; failures raise instead of silently picking a different ordering.

## Verification

`mise test` — `tests/test_routing_ollaya.py` (wire tests run offline; the
`judge` tier needs `pytest --run-judge` plus a live daemon) and
`tests/test_providers_pdfinspector.py` (offline `sys.modules` stub; one live
indexing test skips without the extra).

## Child DOX Index

None — leaf boundary.
