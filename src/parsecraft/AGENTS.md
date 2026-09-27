# AGENTS.md — src/parsecraft/

Primary package source (src layout).

## Purpose

Document intelligence: convert any document into typed structured chunks, with
Markdown as a deterministic projection.

## Ownership

- `ir/` — canonical intermediate representation + Markdown projection
  (see `ir/AGENTS.md`).
- `backends/` — backend protocol + registry (see `backends/AGENTS.md`).
- `config/` — self-contained configuration engine, Route A (see
  `config/AGENTS.md`); zero imports from the rest of the package.
- `assets/` — model-asset manager: downloads, cache, license records (see
  `assets/AGENTS.md`).
- `adapters/` — input adapters that converge sources into the IR (see
  `adapters/AGENTS.md`).
- `routing/` — deterministic auto-mode planner: analysis signals → intent →
  eligible backend candidates, with an injectable judge (see `routing/AGENTS.md`).
- `pipeline/` — routing executor: plan → per-range dispatch → fallback passes →
  aggregate (see `pipeline/AGENTS.md`).
- `environment/` — host probe (installed extras, GPU/VRAM) → `RoutingConstraints`
  bridge (see `environment/AGENTS.md`).
- `providers/` — optional `RoutingJudge` provider implementations, resolved from a
  `provider/model` string and imported lazily (see `providers/AGENTS.md`).
- `cli/` — Typer CLI; commands are plain functions in `commands.py`,
  registered in `app.py` (never decorators in `commands.py`, never import
  `app` there — circular import breaks clean-sort).
- `__about__.py` — package metadata; `__version__` re-exported from
  `parsecraft.__init__` (distribution metadata, fallback `0.0.0`).
- `py.typed` — PEP 561 marker; must stay in the wheel
  (`tests/test_packaging.py`).

## Local Contracts

- Dependency policy: `pyproject.toml` `[project] dependencies` = core only
  (offline-clean); everything heavy lives in extras. New dependencies need
  explicit instruction (`.agents/POLICIES.md`).

## Verification

`mise test` (100% coverage gate) · `mise lint` · `mise typecheck`

## Child DOX Index

- `ir/AGENTS.md` — IR schema + projection rules
- `backends/AGENTS.md` — backend protocol + registry rules
- `config/AGENTS.md` — configuration engine rules
- `assets/AGENTS.md` — asset-manager rules
- `adapters/AGENTS.md` — input-adapter rules
- `routing/AGENTS.md` — routing/auto-mode rules
- `pipeline/AGENTS.md` — executor rules
- `environment/AGENTS.md` — environment-probe rules
- `providers/AGENTS.md` — routing-judge provider rules
