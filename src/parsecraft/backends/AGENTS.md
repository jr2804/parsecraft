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
- Discovery is lazy and never raises: at most one metadata scan per registry
  instance; a broken plugin is recorded in `registry.load_errors`, and
  callers (CLI) MUST surface those — silent omission is a bug.
- Explicit `register()` beats an entry point of the same name (shadowed entry
  point is never loaded).
- Entry-point modules stay light: heavy imports (vLLM, Transformers, model
  code) happen only inside `factory(config)` — proven by
  `tests/test_example_backend.py` against `examples/third_party_backend/`.
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
