# AGENTS.md — src/parsecraft/pipeline/

Auto-mode executor: routing plan → grouped dispatch → aggregated IR document.

## Purpose

`execute(analysis, registry, constraints, source, judge=None, *,
produced_at=None) -> PipelineResult` plans via `plan_route`, bridges
per-page plans to per-range execution, runs fallback passes, and aggregates
a `DocumentResult` with `TraceEntry` records — no silent drops.

## Ownership

- `executor.py` — `execute` and helpers `_group_pages`, `_mergeable`,
  `_execute_group`, `_exception_failure`, `_result_failure`, `_trace`.
- `models.py` — `PassAttempt`, `PageGroup`, `PipelineResult`.
- `__init__.py` — curated re-exports (`execute`, `PageGroup`,
  `PassAttempt`, `PipelineResult`).

## Local Contracts

- Dependency direction: `pipeline` imports `routing`; `routing` stays pure
  (planning only) and never imports `pipeline`.
- Grouping: contiguous pages merge into one range iff they share
  `(intent, chosen, candidates)` AND the chosen descriptor has
  `supports_page_ranges` AND `supports_multi_page`; otherwise one page per
  group. Range = IR `PageRange`; single-page documents get
  `page_range=None`. Returned page numbers must equal the group's pages
  exactly or the attempt fails typed.
- Fallbacks: candidates are tried in plan order; an attempt fails on
  `BackendError` from `create`/`convert` (typed code: `DEPENDENCY_MISSING`
  vs `BACKEND_ERROR`), reported `PassFailure`s (preserved verbatim in the
  trace), or the page-number check. Every attempt emits one `PassAttempt`
  AND `TraceEntry` (`status` ↔ `failure` bijective; `pass_kind` NATIVE for
  native intent, VISUAL for OCR; `settings={"intent": ...}`).
- Exhaustion is loud, not silent: `winner=None` and every page of the group
  gets a `WARNING` diagnostic `pipeline-all-passes-failed`.
- One backend instance at a time (8 GB VRAM ceiling): instances are created
  per attempt and dropped (`del backend`) before the next one — no pool.
  Marked with a `ponytail:` comment at the creation site.
- Constraints come from outside (pc-4's `environment` package via
  `constraints_from_environment`); `execute` never builds its own.
- Heavy imports happen only inside `registry.create` — the instantiation
  boundary; tests isolate the registry by stubbing entry-point discovery.

## Verification

`mise test` — `tests/test_pipeline.py` (100% coverage gate applies; tests
are fully offline with stub factories/backends).
