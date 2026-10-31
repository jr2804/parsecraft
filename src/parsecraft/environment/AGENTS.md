# AGENTS.md — src/parsecraft/environment/

## Purpose

Detect host facts that routing must budget against and bridge them into
`RoutingConstraints` — the detected counterpart of the *declared*
capabilities in `backends/` descriptors.

## Ownership

- `models.py` — `EnvironmentInfo` (frozen value object).
- `probe.py` — `probe_environment()`, `cuda_runtime_note()` and `EXTRA_IMPORTS` (the declared
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
  `tests/environment/test_environment.py` enforces the contract both ways —
  every declared extra is in `EXTRA_IMPORTS` and the two sets are disjoint, so
  no stale name survives in either.
- **Map the import package, not the distribution name.** The value is what
  ``import`` resolves, and a distribution's name can differ from the module it
  installs: ``parsecraft[docling]`` installs ``docling-rs``, which imports as
  ``docling_rs``. Drift here is silent and remote — eligibility drops the
  backend, so auto mode routes to a sibling without a word while a
  ``--no-auto --backend X`` pin reports X ineligible on the very host where it
  works. The extras contract test binds every mapped module to that extra's own
  requirements through `packages_distributions`, so a rename or a swap fails the
  suite instead. An extra that cannot be proved that way is declared in
  `EXTRA_DECLARATIONS` (keyed by extra, honoured only when the operator sets its
  variable — ADR-0010), and the same test requires every declared extra to be
  composite: each of its requirements is also required by a sibling extra. One
  that grows a distinctive requirement is provable again and the declaration
  must go. There is no test-side exemption list any more (pc-5m9).
- `EnvironmentInfo.ocr_backends` is the fact behind `allow_ocr`: the OCR
  harnesses this host can actually run, derived from the descriptors (`is_ocr`
  whose `optional_dependency_group` is installed, or whose required engine is
  present) and never from extra names or engine presence. Both cheaper
  derivations were wrong in shipped builds (pc-r22): matching extras against an
  `ocr-` prefix matched no real extra (`[ocr]` and `[surya]` are the names), so
  installing `[ocr]` permitted nothing, while `or bool(engines)` let pandoc —
  a markup converter — permit OCR. Deriving from descriptors also keeps the
  decision out of the name space, which ADR-0008 decision 13 forbids
  special-casing.
- **Meta extras get no `EXTRA_IMPORTS` entry** (they import nothing, so they
  cannot prove anything): installing one installs the extras it names, and
  those are detected individually. A meta extra is a packaging convenience,
  never a capability — hence none since ADR-0009.

## Verification

`mise test` — `tests/environment/test_environment.py`; the offline guard in
`tests/test_offline_import.py` imports this package.

## Child DOX Index

None — leaf boundary.
