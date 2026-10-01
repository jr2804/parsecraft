# AGENTS.md — src/parsecraft/environment/

## Purpose

Detect host facts that routing must budget against and bridge them into
`RoutingConstraints` — the detected counterpart of the *declared*
capabilities in `backends/` descriptors.

## Ownership

- `models.py` — `EnvironmentInfo` (frozen value object).
- `probe.py` — `probe_environment()` and `EXTRA_IMPORTS` (the declared
  extra → import-package map).
- `constraints.py` — `constraints_from_environment()` bridge: host facts plus
  the plan inputs `formats`, `allow_ocr`, `max_passes`, and `preference`.
- `__init__.py` — curated re-exports (keep `__all__` sorted, `RUF022`).

## Local Contracts

- **Declared vs detected**: descriptors *declare* what a backend can do
  (name, `requires_gpu`, VRAM estimate, formats, `optional_dependency_group`,
  `model_asset`); this package *detects* what this host has — entry points
  present, importable extras, measured VRAM, operator-declared offline.
  Routing/eligibility reads declared capabilities plus these constraints and
  **never probes hardware**; this package never judges eligibility.
- Never import torch/vLLM here: GPU facts come from a bounded
  `nvidia-smi` subprocess (missing/failing/malformed → `vram_budget_gb=0.0`),
  extras from `importlib.util.find_spec` (locates, never imports), offline
  from `PARSECRAFT_OFFLINE` (connectivity is never probed).
- Probe is offline and deterministic: sorted entry points, first GPU only,
  fresh `BackendRegistry` so the process-wide default registry stays
  unpolluted.
- A group missing from `EXTRA_IMPORTS` is not detectable: extend the map when
  a new extra lands in `pyproject.toml`.

## Verification

`mise test` — `tests/test_environment.py`; the offline guard in
`tests/test_offline_import.py` imports this package.

## Child DOX Index

None — leaf boundary.
