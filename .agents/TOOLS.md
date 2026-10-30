# AGENTS.md — TOOLS

Inventory of development-time tooling: CLIs, MCP servers, skills, runtimes,
and project scripts. Each tool appears exactly once, with a short
when-to-use note. This is a live inventory, not a historical log.

## Task runner (mise)

| Tool / task | When to use |
| ----------- | ----------- |
| `mise dev` | Install dependencies (`uv sync --dev`) |
| `mise test` | Run pytest with coverage |
| `mise lint` | ruff + ty + codespell |
| `mise lint-check` / `mise lint` | Lint gate (never rewrites) / explicit lint fixer (`--fix`) |
| `mise format` / `mise run format-check` | Rewriter / canonical-tree gate (rewriters never run inside `mise all`) |
| `mise all` | Full gate: format-check + lint-check + test + typecheck + spell + docs + smoke + actionlint |
| `mise run format-md` | Lint/reformat Markdown with rumdl (not part of `mise all`) |
| `mise run add-mcp-servers <agent>` | Install MCP servers from `.config/mise/data/dev-features.csv` |
| `mise run add-skills` | Install skills from `.config/mise/data/skills.csv` |

## Manifests (single source of truth)

| Manifest | When to use |
| -------- | ----------- |
| `.config/mise/data/dev-features.csv` | Add/remove an MCP server or CLI tool (flip `enabled`, re-run the mise task) |
| `.config/mise/data/skills.csv` | Add/remove a skill (flip `enabled`, re-run the mise task) |
| `.config/mise/scripts/install_dev_features.py` | Installer the two mise tasks invoke; disabled rows are actively removed |
| `.agents/skills/` | Locally installed skill files (demand-loaded per SKILL.md) |

## Python environment

| Tool | When to use |
| ---- | ----------- |
| `uv` | Virtualenvs, installs, running tools (`uvx`), MCP servers via `uvx` |

## Gate linters

| Tool | When to use |
| ---- | ----------- |
| ruff / ty / codespell | `mise lint-check` (gate) / `mise lint` (fixer) / `mise typecheck` / `mise spell` |
| rumdl | Markdown lint/format (`format-md`, `format-check`) |
| pyreorder | Canonical module/statement ordering (`format`, `format-check`) |

## Research CLIs

| Tool | When to use |
| ---- | ----------- |
| ketch | Live-source research: web search, OSS code grep, library docs, page scrape/crawl — bound every fetch, cite every claim |
| Jev (`jev` / `jev-mcp`) | Programmable System One judgments (routing/provider judges) |

## Tracking and scaffolding

| Tool | When to use |
| ---- | ----------- |
| `bd` (beads) | Durable multi-session issue tracking (`.beads/`, prefix `pc`); see `.agents/skills/beads/SKILL.md` |
| copier (+ `copier-uv-plus` skill) | Template renders/updates of this project; see `C:\Users\Jan.Reimes\.agents\skills\copier-uv-plus\SKILL.md` |
