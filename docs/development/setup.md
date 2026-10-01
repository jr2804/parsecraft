---
title: Development setup
---

## Prerequisites

- Python 3.13 (GIL only) or 3.14+ — see [Supported runtimes](../getting-started/installation.md)
- [mise](https://mise.jdx.dev/) — installs the pinned dev toolchain and runs tasks
- [uv](https://docs.astral.sh/uv/) — dependency and environment manager

## First steps

```bash
git clone https://github.com/jr2804/parsecraft.git
cd parsecraft
mise dev        # uv sync -U --dev --all-extras --all-groups
mise test       # pytest with the 100% coverage gate
```

`mise dev` is idempotent; re-run it after dependency changes. `mise install`
provisions the dev toolchain (ruff, ty, rumdl, codespell, pyreorder, and the
optional AI tooling). See [Tech stack](tech-stack.md).

## Tasks

| Task | Runs | Purpose |
| ---- | ---- | ------- |
| `mise dev` | `uv sync -U --dev --all-extras --all-groups` | Install dependencies |
| `mise test` | `uv run pytest --cov=parsecraft` | Tests with the 100% coverage gate |
| `mise lint` | `ruff check src/ tests/ --fix` | Lint and autofix |
| `mise typecheck` | `ty check src/ tests/` | Static type checking |
| `mise spell` | `codespell src/ tests/` | Spell check |
| `mise format` | pyreorder + `ruff format` + import sort | Format Python |
| `mise format-md` | `rumdl fmt` | Lint and format Markdown |
| `mise docs` | `uv run --link-mode=copy zensical build` | Build the docs site |
| `mise all` | `test` + `lint` + `spell` + `format` + `format-md` + `docs` | Full quality gate |
| `mise clean` | remove build artifacts | Clean `build/`, `dist/`, caches |

`mise lint` and `mise format-md` rewrite files in place. Run them before
committing; a clean tree is expected after a full `mise all`.

## Project layout

```text
parsecraft/
├── src/parsecraft/
│   ├── adapters/      # input adapters (Markdown → IR)
│   ├── assets/        # pinned model-asset cache and downloads
│   ├── backends/      # backend protocol and registry
│   ├── cli/           # Typer app and commands
│   ├── config/        # layered configuration engine
│   └── ir/            # canonical IR and Markdown projection
├── tests/             # pytest suite (100% coverage gate)
├── docs/              # Zensical documentation site
├── examples/
│   └── third_party_backend/
├── scripts/
├── .config/mise/      # task and tool configuration
└── .github/workflows/ # CI and release
```

## Pre-commit

```bash
pre-commit install
pre-commit run --all-files
```

The hooks run a subset of the quality gate on every commit.

## Template

The repository is generated from
[copier-uv-plus](https://codeberg.org/jr2804/copier-uv-plus); answers live in
`.copier-answers.yml`. Run `copier update` to pull template changes.
