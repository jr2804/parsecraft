# 00 — Recon: `pdf-inspector.classify_pdf()` as a pre-router signal source

- **Issue:** #3 "Add pdf-inspector pre-router classifier to parsecraft routing"
- **Mode:** READ-ONLY recon. No tracked file modified.
- **Author:** recon agent (pc-1 orchestrator). Decision owner: pc-1.
- **Artifact location:** `.agents/plans/pdf-pre-router/` is intentionally gitignored
  (`.gitignore:75-76`). Correct for mutable working artifacts (`.agents/POLICIES.md`).

All claims below were checked against source in this session; every claim carries
`file:line`. Where the issue text disagrees with the code or upstream, that is
called out explicitly (§0, §3).

---

## 0. Ground truth: the upstream API (issue text is partly wrong)

Verified from `https://raw.githubusercontent.com/firecrawl/pdf-inspector/main/docs/python.md`
(Python API reference; the package's own `readme = "docs/python.md"`).

| Function | Returns | Key fields | Page indexing |
| --- | --- | --- | --- |
| `classify_pdf(path)` / `classify_pdf_bytes` | `PdfClassification` | `pdf_type: str`, `page_count: int`, `pages_needing_ocr: list[int]`, `confidence: float` | **0-indexed** |
| `detect_pdf(path)` / `detect_pdf_bytes` | `PdfResult` | `pdf_type`, `confidence`, `page_count`, `pages_needing_ocr: list[int]`, `pages_with_tables`, `pages_with_columns`, `has_encoding_issues`, `cmap_gaps`, `ocr_reasons_by_page: list[PageOcrReasons]`, `is_complex_layout` | **1-indexed** |
| `extract_pages_markdown(path, pages=None)` | `PagesExtractionResult` | per page `PageMarkdown {page: int, markdown, needs_ocr: bool, ocr_reason: str\|None}`, plus `is_complex`, `pages_with_tables`, `pages_with_columns` | **0-indexed** |

Corrections to the issue text:

1. **Indexing is inconsistent upstream.** `classify_pdf` and
   `extract_pages_markdown` are **0-indexed**; `detect_pdf` is **1-indexed**.
   Parsecraft's IR is strictly 1-based (`ir/models.py` `PageSignal.page_number:
   Field(ge=1)`, `PageRange.start/end: Field(ge=1)`). Any integration must
   normalize and pin this; an off-by-one here routes the wrong page to OCR.
2. **`classify_pdf` is not the richest source.** It has no `ocr_reasons_by_page`
   and no table/column/page-complexity data. `detect_pdf` (1-indexed, and it
   loads no models — "Fast detection only") carries those, including
   machine-readable OCR reasons. If we want tables/columns hints or reasons,
   `detect_pdf` is the better call; `classify_pdf` is the lighter one the issue
   named.
3. **Packaging.** MIT, PyO3/maturin; **prebuilt wheels** for CPython ≥3.8 on
   Linux x86_64/aarch64, macOS Intel/Apple Silicon, Windows x64
   (`docs/python.md` "Install"). No Rust toolchain on those platforms. The
   wheel embeds no models/PDFium/ONNX; those are touched **only** on routed OCR
   work. `classify_pdf`/`detect_pdf` never load them. So "Rust-runtime
   packaging considerations" in the issue reduce to: it is a native (binary
   wheel) optional extra, not a build-from-source requirement — but the OCR
   capability it advertises is **out of scope**; we want detection only.
4. `pdf_type` domain is `"text_based" | "scanned" | "image_based" | "mixed"`
   (Python bindings are snake_case; the Node binding is PascalCase).

---

## 1. Exact current flow

### 1a. `PageSignal` — the only per-page planner input

`src/parsecraft/ir/models.py:229-237`:

```python
class PageSignal(BaseModel):
    page_number: int = Field(ge=1)
    has_native_text: bool
    text_chars: int = Field(ge=0)
    image_count: int = Field(ge=0)
    blank: bool
    replacement_char_ratio: float | None = Field(default=None, ge=0, le=1)
```

Six fields, all text statistics. **No document-type/classification field, no
confidence, no per-page reason.** `PageSignal` is not embedded in
`DocumentResult` (`ir/models.py` `DocumentResult` = metadata/pages/relations/
trace/quality), so it is a planner-input model, not part of the serialized IR
document — relevant to §2(c) schema impact.

### 1b. Where "page needs OCR" is decided — the single decision point

`src/parsecraft/routing/rules.py`:

- `page_needs_ocr(signal)` — `rules.py:154-160`. True when `blank`, or not
  `has_native_text`, or `text_chars < NATIVE_MIN_TEXT_CHARS` (`=40`, `rules.py:19`),
  or `replacement_char_ratio > MAX_REPLACEMENT_RATIO` (`=0.05`, `rules.py:21`).
- `classify_page(signal, page_count, hints)` — `rules.py:143-151`. OCR need
  first; then flavor from **document-level** hints: `tables` and `page_count>1`
  → `OCR_TABLES`; `equations`/`figures` → `OCR_VISION`; else `OCR_GENERAL`;
  otherwise `NATIVE`.
- `extract_hints(analysis)` — `rules.py:77-86`. Document-level `FeatureHints`
  from diagnostic codes `feature:tables|equations|figures` (`rules.py:27-29`)
  and `sum(image_count) >= HEAVY_IMAGE_COUNT` (`=5`, `rules.py:23`).
- `INTENT_RULES` — `rules.py:45-62`: the precedence table **as documentation**
  (the conditions are prose; the executable logic is `page_needs_ocr` +
  `classify_page`).

So OCR-need is per-page (text statistics), while *which OCR flavor* is
document-level. This is exactly the split `pages_needing_ocr` would feed.

### 1c. Signal/diagnostic flow: backend → analysis → plan → executor

```text
backend.analyze(source) -> AnalysisResult{source_hash, page_count, signals, diagnostics}
        (backends/protocol.py:94-100, DocumentBackend.analyze protocol.py:144)
   -> pipeline.analysis.analyze_source(...)            (pipeline/analysis.py:90-108)
        chooses analyzer via choose_analyzer(...)      (pipeline/analysis.py:111-135;
          native-* first, then name, extras-aware via optional_dependency_group)
        registry.create(...); backend.analyze(); del backend
   -> callers: CLI convert (cli/convert.py:81), inspect (cli/inspect.py:51),
        benchmark runner (benchmark/runner.py:115)
   -> routing.plan_route(analysis, backends, constraints, judge)  (planner.py:28-88)
        extract_hints -> per page classify_page (planner.py:53)
        -> in_intent_family filter (planner.py:54) -> degrade (planner.py:58-66)
        -> judge.rank (planner.py:74) -> _validate_order (planner.py:90)
   -> pipeline.execute(...)                            (pipeline/executor.py:68-118)
        plan_route -> _group_pages (executor.py:192) -> _execute_group (executor.py:220)
        pass_kind = NATIVE|VISUAL by intent (executor.py:229),
        settings={"intent": ...}; one ConversionRequest per PageRange
```

Key consequences:

- **Parsecraft routing is already per-page.** `PageRoute` is per page
  (`routing/models.py`), `plan_route` loops page signals (`planner.py:52`), and
  `_group_pages` merges only *contiguous* pages that share
  `(intent, chosen, candidates)` and a range-capable backend
  (`executor.py:212-217`). The issue's "per-page OCR routing — a capability
  none of the current parsecraft backends expose cleanly" describes the
  *backend* `analyze()` surface; the planner already dispatches per page.
- **Diagnostics are the document-level hint channel.** `AnalysisResult.diagnostics`
  (protocol.py:100) is read only by `extract_hints` (rules.py:79) and `inspect`
  rendering (cli/inspect.py:79).
- **Feature-hint emission is currently under-fed in production.** Only
  `native-pdf` emits diagnostics today: `pdf-encrypted` and
  `pdf-no-extractable-text` (native/pdf.py:84, 96). No production backend
  emits `feature:tables|equations|figures`; only tests synthesize them from
  `tests/fixtures/sources.toml` `features` (`tests/test_routing.py:399-410`).
  So the channel exists and is tested, but real PDFs get no flavor hints from
  the built-in analyzer.

---

## 2. Where `classify_pdf` could attach — three options

Evaluation axes, per task: determinism, offline-import gate
(`tests/test_offline_import.py`, root AGENTS.md rule 3), code-owned hard
constraints (ADR-0004 §3), injectable-seam precedents (`LanguageDetector`,
`RoutingJudge`).

### (a) Backend-owned: the analyzer's `analyze()` emits richer signals/diagnostics

Two shapes:

- **(a1) Enrich `native-pdf.analyze()`** (`native/pdf.py:65-101`) — call
  pdf-inspector when its extra is installed; fold `pages_needing_ocr` into
  `PageSignal` and emit classifier diagnostics; else keep the pypdf path
  (`native/pdf_inspect.py`).
- **(a2) New `pdf-inspector` `DocumentBackend`** whose `analyze()` calls
  `classify_pdf`/`detect_pdf`.

Pros

- No core contract change; classifier facts become plain data
  (`PageSignal` + `Diagnostic`), matching the existing "analyze produces
  signals" contract (protocol.py:144).
- Reuses the registry/entry-point extension surface and the extras-aware
  analyzer choice (`pipeline/analysis.py:118-137`).
- Deterministic per input bytes + installed environment; the pure planner is
  untouched.
- Offline-import gate: a heavy/native import lives inside an impl module loaded
  via `import_module(...)` at instantiation — exactly the pattern
  `native/pdf.py:_inspect_impl` / `_extract_pdf` already uses.

Cons

- **(a1)** silently changes the canonical PDF analyzer's signal source with the
  installed environment: the same bytes produce different `PageSignal`s (and
  therefore different plans) depending on whether pdf-inspector is installed.
  It also couples the light `pdf-lite` pypdf path to a second PDF library.
  Provenance (`pdf-inspector` version) would have to be recorded or plans are
  not reproducible across environments.
- **(a2)** `choose_analyzer` ranks `not name.startswith("native-")` first
  (`pipeline/analysis.py:135-137`), so a backend named `pdf-inspector` can
  **never** win over `native-pdf`; it would only be reached by renaming it
  `native-*` or changing the selection key. And a classifier-only backend still
  must implement `convert()` (`DocumentBackend` protocol, protocol.py:141-150);
  pdf-inspector can convert, but that is a new converter feature well beyond
  issue #3's scope.
- `PageSignal` has no confidence/reason field, so classifier confidence and
  `ocr_reasons_by_page` are lossy (diagnostics message-only) unless (c) also
  lands.
- The 0-index → 1-index normalization lives inside the backend; correctness is
  per-backend rather than one shared fold.

### (b) Pipeline-level injectable classifier seam (mirror judge / `LanguageDetector`)

Shape: core defines a `Protocol` (e.g. `PdfClassifier.classify(source) ->
PdfClassificationFacts`); an implementation lives in an optional extra and is
imported lazily; the caller injects it at the **analysis boundary**
(`analyze_source(..., classifier=...)`), where a small pure fold merges
classifier facts into the analyzer's `AnalysisResult`. `plan_route` keeps its
current signature and stays pure.

Precedents this mirrors:

- `LanguageDetector` (`routing/language.py:17-21`) is a core Protocol; the
  implementation is optional (`providers/ollaya.py:106`, `load_language_detector`
  at `providers/ollaya.py:232`); the core carries the *result* as plain data on
  `RoutingConstraints.language` and never imports the implementation
  (`tests/test_routing_language.py:108` pins this).
- `RoutingJudge` (`routing/judge.py:33`) is injected into `plan_route`/`execute`;
  `resolve_judge` (`routing/judge_providers.py:69-93`) resolves a spec lazily.

Pros

- Matches both established seam patterns; core stays offline-import-clean
  because only the Protocol + facts model live in core.
- Optional by construction: **no classifier → today's behavior exactly**. The
  fallback is not a new code path.
- Planner stays pure and deterministic; the fold is a pure function of
  `(analysis, facts)`.
- Testable with a stub; no PDF-specific concept is forced into `plan_route`.

Cons

- New public seam (Protocol + facts model + resolver + CLI/config flag + tests
  - docs) — more surface than (a1).
- Precedence must be decided: does a classifier verdict **override**
  `page_needs_ocr` (pypdf text stats) or only **augment** it? If it overrides,
  `classify_page`/`page_needs_ocr` need an explicit "classifier says OCR"
  input; if it augments, the fold encodes the verdict into existing
  `PageSignal` booleans (lossy for confidence).
- Two placement choices: fold **before** `plan_route` (preferred — planner
  untouched) vs pass an extra hint index **into** `plan_route` (signature
  change; every caller: executor.py:91, cli/inspect.py:66,
  benchmark/runner.py:130, plus tests).

### (c) New `AnalysisResult` / `PageSignal` fields (contract change)

Shape: e.g. `PageSignal.pdf_type: PdfType | None`,
`PageSignal.needs_ocr_confidence: float | None`,
`PageSignal.ocr_reason: str | None`; or a document-level
`AnalysisResult.classification: PdfClassificationFacts | None`.

Affected consumers (every read of `AnalysisResult`/`PageSignal` found):

- `src/parsecraft/ir/models.py` — `PageSignal` itself. **Schema note:**
  `DocumentResult` does not embed `PageSignal`, so `SCHEMA_VERSION = "1"`
  (`ir/models.py`) does **not** need a wire-format bump; but `PageSignal` is a
  public pydantic model — pc-1 must decide whether it counts as "IR schema".
- `src/parsecraft/backends/protocol.py:94-100` — `AnalysisResult`.
- Every `analyze()` constructor: `native/_common.py:108`, `native/pdf.py:100`,
  `liteparse/_impl.py:175`, `docling/_impl.py:122`, `pandoc/_impl.py:124`,
  the four `ocr/*_impl.py` (lines 73/76/77/129), `ocr/_common.py:238`,
  `examples/third_party_backend/.../impl.py`. (New optional fields default to
  `None`, so constructors compile; but each is a place a reviewer must check.)
- `src/parsecraft/routing/rules.py` — `page_needs_ocr` (154), `classify_page`
  (143), `extract_hints` (77).
- `src/parsecraft/routing/planner.py:52-53` — reads signals.
- `src/parsecraft/pipeline/executor.py:108, 230` — `source_hash`, `page_count`
  only (unaffected, but reviewed).
- `src/parsecraft/pipeline/analysis.py:90-108` — producer.
- `src/parsecraft/cli/inspect.py:72-79` — `render_preview` prints signal fields;
  `preview_payload` (inspect.py:90) serializes to JSON — **public CLI JSON
  schema changes**; assertions in `tests/test_cli_inspect.py:144-162`.
- `src/parsecraft/benchmark/runner.py:145, 178` — `analysis.page_count`,
  `signal.text_chars`.
- Tests: `tests/test_routing.py` (signal fixtures/hints), `test_cli_inspect.py`,
  `test_backends_native.py`, `test_pipeline.py`, `test_benchmark.py`.
- Docs: `docs/architecture/routing.md` ("Signal to intent" tables),
  `docs/reference/api.md` (mkdocstrings auto-generates from `parsecraft.ir` /
  `parsecraft.backends`).

Pros

- Richest data: `pdf_type`, confidence, per-page OCR reasons, table/column
  pages all become typed and traceable; one channel for every consumer.
- Directly matches the issue's "folds the result into `AnalysisResult`".

Cons

- Puts PDF-specific concepts (`pdf_type`) into the generic IR layer where they
  are meaningless for html/txt/md sources.
- Widens the frozen public contract and every consumer/test/doc above; the
  cost is borne even by backends that never emit the fields.
- Confidence semantics are analyzer-dependent (pdf-inspector's confidence is
  not comparable to a text statistic), so the field needs a documented
  provenance/meaning or it invites misuse.

### Recommendation (for pc-1 to accept/reject)

**Primary: (b) as the architecture, folding into existing data where lossless.**
A classifier is a *signal source*, not a converter and not a decision authority;
the two existing precedents (`LanguageDetector` = data producer, `RoutingJudge`
= injected re-ranker) both say "optional core Protocol, lazy implementation,
pure data into the planner". Use `detect_pdf` (1-indexed, carries
`pages_needing_ocr` + reasons + tables/columns) if reasons/hints are wanted;
`classify_pdf` (0-indexed) if only the per-page OCR list is needed.

**(a1) is rejected as a default** because it silently changes the canonical
PDF analyzer's output with the installed environment and mixes two PDF
libraries in one analyze path.

**(c) is deferred** to the point where confidence/provenance must be
*persisted* rather than consumed transiently. If pursued, prefer a
document-level `AnalysisResult` facts record over PDF-specific fields on
`PageSignal`, and decide explicitly whether `PageSignal` is "IR schema".

---

## 3. The issue's fallback claim is wrong — precise correction

Issue text: *"Falls back to the existing deterministic judge when pdf-inspector
is unavailable."*

This is a **category error**, confirmed against source:

- `plan_route` **always** classifies intent first: `classify_page(signal, ...)`
  at `planner.py:53`, backed by `page_needs_ocr` (`rules.py:154`). The judge is
  consulted **after** intent and eligibility: `active_judge = judge if judge is
  not None else DeterministicJudge()` (`planner.py:44`), then
  `active_judge.rank(intent, family)` (`planner.py:74`).
- `DeterministicJudge` only **orders** candidates (`judge.py:41-47`); it never
  reads signals, never classifies, and never decides OCR-need. ADR-0004 §4
  states this ("may re-rank or drop candidates; it may not add one"), and
  ADR-0004 §5 + `_validate_order` (`planner.py:90-99`) enforce it.

**Correct statement:** the classifier-unavailable fallback is the existing
**deterministic `INTENT_RULES` heuristics** — `page_needs_ocr` +
`extract_hints` + `classify_page` in `routing/rules.py`. When no classifier is
present, `analyze_source` returns the chosen analyzer's own `AnalysisResult`
unchanged and `plan_route` classifies with those rules exactly as today. There
is **no new fallback path**: absence of a classifier *is* the current behavior.
The judge must not be named as the fallback anywhere (issue body, ADR, docs).

---

## 4. ADR-0004 extension — decision points (pc-1 owns `docs/adr/`)

Not a rewrite; an extension/amendment recording:

1. **Existence of a classifier seam.** An optional per-page OCR-need
   classifier may feed the planner. State the relationship to decision §2 (the
   rule table): does the classifier **override** `page_needs_ocr` or only
   **augment** document-level hints? Define precedence explicitly.
2. **Fallback is the rule table, not the judge.** Codify §3 above: unavailable/
   absent classifier → `INTENT_RULES` unchanged; the judge stays a re-ranker
   (decisions §4-§5 untouched).
3. **Classifier is not an authority.** It cannot widen eligibility, override
   `allow_ocr`/VRAM/offline/licence constraints, or route a native-intent page
   to OCR — the code-owned funnel (ADR-0004 §3) remains the only authority.
4. **Data contract.** Where the classifier verdict lives: transient fold into
   `AnalysisResult` vs a new field; whether the verdict is persisted in
   `DocumentResult`/trace; whether `PageSignal` counts as IR schema.
5. **Provenance & determinism.** The plan is only reproducible if the
   classifier identity/version is known; decide whether that is recorded
   (trace/quality) and pinned, analogous to `model_revision`.
6. **Page-number normalization.** Upstream 0-indexed (`classify_pdf`,
   `extract_pages_markdown`) vs 1-indexed (`detect_pdf`) vs IR 1-based
   (`PageSignal.page_number`). Pin the conversion rule.
7. **Licensing/packaging.** MIT, native binary wheel, optional extra; OCR
   runtime (PDFium/ONNX/models) explicitly out of scope; must satisfy the
   jointly-resolvable extras rule (root AGENTS.md rule 10) and the
   backend-registration rule (rule 5) if it becomes a backend.
8. **Per-page over document-level priority** (issue explicitly asks this be
   captured): per-page classifier verdict outranks document-level `pdf_type`,
   with document-level used only for flavor hints.

---

## 5. Open questions for pc-1

> **DECIDED 2026-10-01 (pc-1).** Answers inline below each question, binding
> for implementation. Summary: `detect_pdf`; augment-only; analysis-boundary
> fold carrying one generic nullable `PageSignal.classifier_needs_ocr`;
> seam in `routing/classifier.py`, impl in `providers/pdfinspector.py`; no
> backend in #3; no confidence logic; provenance via diagnostic; tables hint
> included; offline-legal; two beads gated on #2.

1. **`classify_pdf` or `detect_pdf`?**
   > **Decision: `detect_pdf`.** 1-indexed like the IR — kills the
   > normalization trap at the source instead of converting per caller — and
   > one call yields OCR-need + reasons + tables/columns (Q8 needs those
   > anyway). Detection only; its OCR runtime is out of scope. Pin the exact
   > upstream call + field semantics in the impl docstring. Cost/no-model pin
   > (user-confirmed requirement): detection is a pure-Rust **text-layer
   > structural scan, ~20–100 ms, loads NO models** — a classifier impl that
   > loads a model (OCR or otherwise) is OUT OF CONTRACT. Deviation from the
   > issue text is deliberate; recorded in ADR + issue comment. `detect_pdf` is 1-indexed and carries
   `pages_needing_ocr` **plus** `ocr_reasons_by_page`, `pages_with_tables`,
   `pages_with_columns`; `classify_pdf` is 0-indexed and lighter. The issue
   named `classify_pdf`; the richer call may remove a second library.
2. **Override or augment?** Does a classifier verdict replace
   `page_needs_ocr`'s text-statistics decision, or only add flavor hints /
   override when it disagrees? This determines whether `plan_route` needs any
   input change.
   > **Decision: augment-only, asymmetric.** OCR-need =
   > `page_needs_ocr(signal) OR (signal.classifier_needs_ocr is True)`. The
   > classifier may ADD OCR-need, never remove it: text-stats OCR-need
   > (blank/thin/garbled) means native extraction genuinely fails, so a
   > `text_based` verdict must never force NATIVE on such a page. Over-routing
   > to OCR costs money; under-routing loses content — POLICIES priority 1 is
   > correctness. The asymmetry is encoded in the field type (Q4): `True`-only
   > semantics; `False` is never a verdict. Mirror of the judge rule: the
   > classifier widens OCR candidacy, never narrows eligibility.
3. **Seam shape:** a generic `PageOcrClassifier` Protocol (option b) vs a
   PDF-specific one; and where its implementation lives — `providers/` (like
   ollaya) or a `backends/native/` impl module (like pdf-inspect).
   > **Decision: role-named core seam + provider impl.**
   > `routing/classifier.py`: `PageOcrClassifier` Protocol + `OcrFacts` model
   > (frozen; generic fields: `pages_needing_ocr`/`pages_with_tables` as
   > IR-1-based `frozenset[int]`, `pdf_type`, `confidence`, `source`). Facts
   > page numbers are IR 1-based by CONTRACT — the provider normalizes once;
   > nothing downstream converts. Implementation: `providers/pdfinspector.py`
   > with `load_classifier` (lazy `import_module`, mirrors providers/ollaya);
   > `resolve_classifier(spec | instance | None)` in the same module as the
   > seam, `None → None` — NO default implementation, absence is exactly
   > today's behavior (unlike the judge, which has DeterministicJudge).
   > Role-named (not PDF-named) because the planner consumes OCR-need facts,
   > whatever future source produces them; the PDF shape lives in the facts
   > model, not the Protocol name.

4. **Injection point:** `analyze_source(...)` fold (preferred, planner pure) vs
   a new per-page-hint argument on `plan_route` (signature change for
   executor/CLI/benchmark/tests).
   > **Decision: fold at the analysis boundary, planner untouched.**
   > `plan_route` signature unchanged. Pure
   > `apply_classifier(analysis, facts) -> AnalysisResult` in
   > `pipeline/analysis.py`: (i) sets
   > `PageSignal.classifier_needs_ocr: bool | None = None` — ONE new generic
   > nullable field, no PDF types, no confidence on the model (minimal slice
   > of (c), chosen because augment-only needs a typed carrier and mutating
   > `has_native_text` would falsify data); (ii) appends the provenance
   > diagnostic (Q7) and tables hint (Q8). `page_needs_ocr` gains the OR-term.
   > Default `None` → constructors compile; CLI inspect JSON gains one
   > nullable key (update `test_cli_inspect` assertions — accepted churn).
5. **Is a pdf-inspector *backend* in scope at all?** It can also extract/
   convert (strong benchmark scores), but issue #3 is classification-only.
   Adding `convert()` is a separate feature; and `choose_analyzer`'s
   `native-*`-first key means it cannot become the PDF analyzer without a
   selection-rule change.
   > **Decision: no backend in #3.** Issue #2 (pc-2, in flight) ships the
   > pdf-inspector backend family entry with a classify surface. #3 adds zero
   > backend code; the provider imports `pdf_inspector` directly via
   > `import_module`, independent of the backend module. One thin duplicated
   > wrapper call across two lazily-loaded tiers is acceptable — sharing a
   > helper would couple the tiers. `choose_analyzer`'s native-first key stays
   > untouched.
6. **Confidence handling:** discard it, record it in diagnostics, or promote it
   to a typed field (option c)? Is a confidence floor needed before a verdict
   may override text statistics?
   > **Decision: no floor, no decision logic.** Augment-only (Q2) removes the
   > failure mode a floor would guard: a wrong verdict can only add OCR
   > passes, never suppress native extraction. Confidence is consumed by no
   > rule; it is recorded verbatim in the provenance diagnostic (Q7) for
   > humans, not read by code.
7. **Persistence/provenance:** must the classifier identity/version appear in
   `DocumentResult` trace/quality for reproducibility, or is a transient fold
   acceptable?
   > **Decision: transient fold + provenance diagnostic; deep persistence
   > deferred.** The fold emits one document-level diagnostic carrying
   > classifier name, upstream version, document `pdf_type`, confidence —
   > enough to reconstruct why plans differ across environments. No
   > `DocumentResult`/trace schema change; revisit only if a consumer must
   > machine-read provenance. ADR determinism statement: identical inputs +
   > identical installed classifier version → identical plan.
8. **Feature hints from the classifier:** should `pages_with_tables`/
   `pages_with_columns` become `feature:tables`/`feature:figures` diagnostics
   (filling the currently under-fed production channel, §1c), or is that a
   > **separate bead?**
   > **Decision: include in #3.** Emission is one fold step over data
   > `detect_pdf` already returns; the channel + its tests exist
   > (`extract_hints`, test_routing). Emit `feature:tables` when
   > `pages_with_tables` is non-empty. Do NOT synthesize `feature:figures` —
   > no upstream signal maps to it honestly. Hints only select OCR flavor for
   > pages already needing OCR, so the augment-only asymmetry holds. Same
   > bead, not a separate one.
9. **Offline gate wording:** does an `offline=True` constraint forbid invoking
   a locally installed pdf-inspector (pure-Rust, no network)? Today `offline`
   > **only excludes backend `model_asset` carriers (`rules.py:98`); a
   > classifier is not a backend, so the rule needs an explicit statement.**
   > **Decision: offline does not forbid the classifier; local-only is a
   > Protocol contract.** `offline` governs backend candidate eligibility and
   > stays untouched. The Protocol docstring pins: implementations MUST be
   > local-only — no network, no model downloads — and the provider test
   > asserts resolution + call without network. A network-dependent classifier
   > would be a new ADR.
   > **Strengthened (user question 2026-10-01):** "no model downloads" →
 > **"no model LOADS"** — the classifier must stay a structural text-layer
   > scan (tens of ms); using an ML model to decide whether to run an ML
   > backend is oversized and forbidden by contract. Rationale recorded: the
   > scan runs in analyze() before conversion is chosen, so its cost must stay
   > negligible against the conversions it routes (native ~ms–s, docling/OCR
   > s–min).
10. **Bead/scope:** is #3 tracked as a bead with an implementation plan, and
    which parts (seam, ADR, docs, tests) are one deliverable vs several?
    > **Decision: two dependency-ordered beads, BOTH gated on #2 landing**
    > (shared `pyproject.toml`: extra + entry point must exist first; the
    > provider's tests need pdf_inspector in `.venv`).
    > - Bead A `classifier seam` — `routing/classifier.py` (Protocol +
    >   `OcrFacts` + `resolve_classifier`), `apply_classifier` fold,
    >   `classifier_needs_ocr` field + rules OR-term, unit tests, **ADR-0004
    >   amendment — pc-1 writes this, not the worker**.
    > - Bead B `classifier provider + wiring`, depends-on A —
    >   `providers/pdfinspector.py` (`load_classifier`, lazy), CLI
    >   `--classifier` flag on convert (spec string, judge-style resolution),
    >   integration tests (mixed PDF routes per page; absent → unchanged),
    >   `docs/architecture/routing.md` + pipeline mermaid;
    >   `docs/reference/api.md` auto-covers.
    > pc-1 posts the scope-review comment on GitHub #3; workers implement
    > against the ADR + this doc, NOT the issue body.

---

## 6. Verification notes

- No file under version control was modified; this document lives in the
  gitignored plans tier. `git status --short` showed only a pre-existing
  `.gitignore` modification unrelated to this recon.
- Upstream API facts were read from the project's own `docs/python.md` on
  `main` (PyPI package version line in `pyproject.toml` was `1.25.2`); no local
  install was performed.
- Source citations are line-numbered against the working tree at recon time
  (HEAD `fa82cc0`).
