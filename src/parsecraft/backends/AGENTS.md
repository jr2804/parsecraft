# AGENTS.md — src/parsecraft/backends/

Backend protocol, descriptors, and registry — the extension surface.

## Purpose

Define what a backend is (`DocumentBackend`), how it is instantiated
(`BackendFactory`), and how implementations are discovered (explicit
registration + Python entry points) without editing this package.

## Ownership

- `protocol.py` — public protocol + request/result/descriptor models.
- `registry.py` — `BackendRegistry`, `default_registry`,
  `ENTRY_POINT_GROUP`, and the `suffixes()`/`suffixes_for()` projection.
- `errors.py` — `BackendError` hierarchy incl. recorded `BackendLoadError`.
- `__init__.py` — curated re-exports (keep `__all__` sorted).

## Local Contracts

- Frozen entry-point group: `parsecraft.backends`
  (`ENTRY_POINT_GROUP` is the single definition — ADR-0001 §1).
- `supported_formats` is a **capability** statement in **MIME media types** (one
  vocabulary across every backend family), never a file-discovery list — the
  lossy extension→MIME mapping belongs to `pipeline.MEDIA_TYPES`, which stays
  the single source. `registry.suffixes()`/`suffixes_for()` (pc-53p) are the
  public PROJECTION of that table joined with the descriptors; they are never a
  second table, and their `pipeline`/`environment` imports are function-local
  because both packages import `parsecraft.backends`.
- Discovery is lazy and never raises: at most one metadata scan per registry
  instance; a broken plugin is recorded in `registry.load_errors`, and
  callers (CLI) MUST surface those — silent omission is a bug.
- **Locking (pc-4u7.32):** registry state (registration, discovery,
  `load_errors`) is guarded by an internal re-entrant lock; discovery is
  single-flight (a concurrent first user waits instead of re-scanning). The
  lock is NEVER held across `factory(config)` or `convert()` — heavy model
  loads and all backend work run outside it, so lookups never stall behind
  a load. Instances are caller-owned: `create()` never memoizes and never
  shares, so residency/VRAM admission stays with the caller — the pipeline
  executor swaps one instance at a time within the 8 GB VRAM ceiling, and a
  concurrent caller must budget for every instance it holds itself (no
  registry-side admission control until Phase 7 GPU).
- Explicit `register()` beats an entry point of the same name (shadowed entry
  point is never loaded).
- Entry-point modules stay light. A backend with heavy or optional dependencies
  keeps them in a **separate implementation module** imported only at
  instantiation via `importlib.import_module(...)` — never an inline `import`,
  which `pyreorder` (`hoist_inline_imports`) hoists to module level and breaks.
  Dependency-free backends may import their implementation at module top level.
- Built-in backends live under `backends/<family>/<name>.py` with a light
  `factory` object (descriptor + `__call__`) declared in `pyproject.toml` under
  `[project.entry-points."parsecraft.backends"]`. Each optional dependency gets
  its own extra.
- `BackendDescriptor` is pure data (no factory field); the registry binds
  descriptor↔factory internally.
- `ConversionRequest` carries all bounds (page range, timeout, cancellation,
  output/context budgets) — backends must honor them or return a typed
  `PassFailure`.
- **Per-page generation budget is pinned, never inherited from the pipeline**
  (pc-c8t): both transcribers pass
  `_common.DEFAULT_PAGE_MAX_NEW_TOKENS` when the request names no
  `max_context_tokens`, because the transformers pipeline's own default is an
  undocumented implementation detail and 256 of them truncates a dense page.
  One number for both runtimes so a page cannot get a different budget per
  runtime.
- **Tokenizer/processor loads of VLM backends go through
  `backends/ocr/_common.tokenizer_load_kwargs()`** (pc-scr): it carries the
  corrections every tokenizer needs, currently `fix_mistral_regex=True` — several
  VLM repos ship a broken Mistral-family pre-tokenizer regex and transformers
  warns that tokenization is wrong until the flag is passed. A load site that
  builds its own kwargs dict silently tokenizes differently from its siblings.
  No runtime fallback is added anywhere: the kwarg is guaranteed by the
  declared `TRANSFORMERS_RANGE` (see `TOKENIZER_FIX_KWARGS` in `_common.py`
  for the canonical statement).
  Corollary verified against transformers 5.18: `pipeline()` resolves the
  processor with only its hub/model kwargs, so the flag CANNOT be passed through
  `pipeline(...)`; a backend that needs it loads the processor itself and hands
  the pipeline the instance.

## Work Guidance

Third-party example: `examples/third_party_backend/` (standalone
distribution, entry point declared in its own `pyproject.toml`).

## Verification

`mise test` — `tests/test_backends_registry.py`,
`tests/test_example_backend.py`, `tests/test_offline_import.py`.

## Child DOX Index

- `native/AGENTS.md` — in-package native family (no optional extra)
- `docling/AGENTS.md` — `docling` extra: light/heavy split, structured table rows
- `pandoc/AGENTS.md` — `pandoc` extra: system binary, single logical page
- `pdf_inspector/AGENTS.md` — `pdf-inspector` extra: PDF-only, page-index mapping

The `liteparse` and `ocr` families stay under this parent's contract.
