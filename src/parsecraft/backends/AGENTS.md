# AGENTS.md — src/parsecraft/backends/

Backend protocol, descriptors, and registry — the extension surface.

## Purpose

Define what a backend is (`DocumentBackend`), how it is instantiated
(`BackendFactory`), and how implementations are discovered (explicit
registration + Python entry points) without editing this package.

## Ownership

- `protocol.py` — public protocol + request/result/descriptor models.
- `registry.py` — `BackendRegistry`, `default_registry`,
  `ENTRY_POINT_GROUP`.
- `errors.py` — `BackendError` hierarchy incl. recorded `BackendLoadError`.
- `__init__.py` — curated re-exports (keep `__all__` sorted).

## Local Contracts

- Frozen entry-point group: `parsecraft.backends`
  (`ENTRY_POINT_GROUP` is the single definition — ADR-0001 §1).
- `supported_formats` is a **capability** statement in **MIME media types** (one
  vocabulary across every backend family), never a file-discovery list —
  consumers own the lossy extension→MIME mapping.
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

## Work Guidance

Third-party example: `examples/third_party_backend/` (standalone
distribution, entry point declared in its own `pyproject.toml`).

## Verification

`mise test` — `tests/test_backends_registry.py`,
`tests/test_example_backend.py`, `tests/test_offline_import.py`.
