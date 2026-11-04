# AGENTS.md — src/parsecraft/config/

Generic layered configuration engine (ADR-0001 §5, Route A).

## Purpose

Provide a self-contained, vendorable config engine: ordered layers, per-key
provenance, secret placeholders, and strict schema validation. It carries no
document, format, model, or pipeline concepts.

## Ownership

- `models.py` — `ConfigLayer`, `LAYER_ORDER`, `ConfigSource`, `ConfigWarning`,
  `ConfigCheckReport`, `ConfigShowEntry`, `ConfigSchema`.
- `engine.py` — `ConfigEngine` and the internal merge/expand/resolve helpers.
- `errors.py` — `ConfigError` hierarchy.
- `__init__.py` — curated re-exports (keep `__all__` sorted).

## Local Contracts

- **Self-contained.** Zero imports from `parsecraft.ir`, `parsecraft.backends`,
  `parsecraft.cli`, or any other ParseCraft package. The module must stay
  splittable or vendorable (ADR-0001 §5).
- **Layer precedence is fixed:** `default → global → project → env → cli`
  (`LAYER_ORDER`). Later layers override earlier ones; every effective leaf
  records a `ConfigSource`.
- **A file layer can hold several files.** `read_files(layer, paths)`
  deep-merges them in order: a later file wins per key, and every effective
  leaf keeps the `ConfigSource` of the exact file that set it (so `config
  show` reports `project:<path>` per key) while `path_keys` resolve against
  that declaring file. `read_file` still replaces the whole layer, and an
  empty `paths` sequence leaves the layer empty.
- **Env is read explicitly** by `read_env()`; `ConfigSchema` disables native
  pydantic-settings sources so nothing bypasses provenance.
- **Paths resolve against the declaring file**, never CWD (ADR-0001 §5). Keys
  listed in `path_keys` resolve relative to the source file's directory.
- **Secrets are references, never literals.** `${VAR}` placeholders resolve from
  the environment; a missing variable raises `ConfigSecretError`.
- **Writes go through tomlkit** (`migrate`), preserving file style.
- **Global directory comes from platformdirs** (`global_config_path`), never a
  hard-coded XDG or `~/.cache` path.
- **Snapshots are deterministic:** `snapshot()` sorts keys recursively.

## Work Guidance

- **Keep `parsecraft.toml.example` in sync.** Any change here — and any change
  to the schema this engine loads (`ParsecraftConfig` in `cli/config.py`) —
  must be checked against `parsecraft.toml.example` in the project root and
  updated when keys, defaults, locations, or semantics shift. The guard test
  `test_example_config_matches_the_schema` (tests/cli/test_config.py) fails
  when a schema key is missing from the example; comment and semantic drift
  stay on the editor.
- Adding a layer or source means updating `LAYER_ORDER`, `_effective`, and the
  provenance tests together.
- Keep helpers pure and module-level; `ConfigEngine` methods record state only.
- Strictness lives in the schema (`extra="forbid"`); surface failures as
  `ConfigValidationError` with the dotted location.

## Verification

`mise test` — `tests/config/test_config.py`. 100% coverage is enforced; new branches
need tests. `mise lint` and `mise typecheck` must stay clean.

## Child DOX Index

None.
