# AGENTS.md — src/parsecraft/environment/

## Purpose

Detect host facts that routing must budget against and bridge them into
`RoutingConstraints` — the detected counterpart of the *declared*
capabilities in `backends/` descriptors.

## Ownership

- `models.py` — `EnvironmentInfo` (frozen value object).
- `probe.py` — `probe_environment()`, `cuda_runtime_note()` and`EXTRA_IMPORTS` (the declared
  extra → import-package map).
- `constraints.py` — `constraints_from_environment()` bridge: host facts plus
  the plan inputs `formats`, `allow_ocr`, `max_passes`, and `preference`.
- `__init__.py` — curated re-exports (keep `__all__` sorted, `RUF022`).

## Local Contracts

- **Declared vs detected**: descriptors *declare* what a backend can do
  (name, `gpu_requirement`, VRAM estimate, formats, `optional_dependency_group`,
  `model_asset`); this package *detects* what this host has — entry points
  present, importable extras, measured VRAM, operator-declared offline.
  Routing/eligibility reads declared capabilities plus these constraints and
  **never probes hardware**; this package never judges eligibility.
- Never import torch/vLLM here: GPU facts come from a bounded
  `nvidia-smi` subprocess (missing/failing/malformed → `vram_budget_gb=0.0`)
  plus `cuda_runtime_note()`, which reads the installed torch build's own
  `version.py` (`cuda = None` for a `+cpu` wheel) — metadata only, so a probe on
  a CPU-only host stays millisecond-cheap. Extras come from
  `importlib.util.find_spec` (locates, never imports), offline from
  `PARSECRAFT_OFFLINE` (connectivity is never probed).
- **Hardware ≠ runtime**: `vram_budget_gb` is what `nvidia-smi` reports;
  `gpu_usable` is whether the runtime could use it. A GPU-only backend is
  dropped by eligibility unless `gpu_usable` is true, and the CLI warns once when
  a GPU is present but unusable. Report an unreadable/silent torch metadata file
  as a reason (via `cuda_runtime_note()`), never as a guess.
- Probe is offline and deterministic: sorted entry points, first GPU only,
  fresh `BackendRegistry` so the process-wide default registry stays
  unpolluted.
- A group missing from `EXTRA_IMPORTS` is not detectable, so the planner never
  sees it installed: extend the map when a new extra lands in `pyproject.toml`.
  `tests/test_environment.py` enforces the contract both ways — every declared
  extra is in `EXTRA_IMPORTS` **or** in `META_EXTRAS`, the two sets are
  disjoint, and no stale name survives in either.
- `META_EXTRAS` holds the extras that exist only to pull other extras in
  (`auto`). They import nothing, so they must never get an `EXTRA_IMPORTS`
  entry: installing one installs the extras it names, and those are detected
  individually. A meta extra is a packaging convenience, never a capability.

## Verification

`mise test` — `tests/test_environment.py`; the offline guard in
`tests/test_offline_import.py` imports this package.

## Child DOX Index

None — leaf boundary.
