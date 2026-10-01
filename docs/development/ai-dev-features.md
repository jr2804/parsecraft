---
title: AI dev features
---

ParseCraft ships optional, opt-in tooling for AI coding agents. Nothing installs
by default; two mise tasks read CSV manifests and install the tools, MCP
servers, and skills they describe.

## Enable

```bash
mise run add-mcp-servers claude    # install tools and register MCP servers
mise run add-skills                # install agent skills
```

`add-mcp-servers <agent>` takes the target agent name (`claude`, `gemini`,
`copilot`, `opencode`, ...). Both tasks depend on `mise dev`, so dependencies
install first.

## Manifests

The CSVs in `.config/mise/data/` are the single source of truth. Edit them and
re-run the task to change the toolset.

`.config/mise/data/dev-features.csv` columns:

| Column | Purpose |
| ------ | ------- |
| `category` | Grouping used in this page |
| `tool` | mise tool spec installed with `mise use <tool>@latest` |
| `tool_value` | Reserved for a pinned version |
| `mcp_command` | Command that starts the MCP server |
| `mcp_name` | Name registered with `add-mcp` |
| `skill_repo` / `skill` | Skill repository and skill name |

`.config/mise/data/skills.csv` has `category`, `repo`, `skill`.

## Categories

| Category | Examples |
| -------- | -------- |
| `slop` | sloppylint, ai-slop-detector, vibecheck, aislop, fartrun, CytoScnPy |
| `codebase` | grepai (semantic search), codegraph (symbol graph), repowise (architecture), ketch (live-source research) |
| `dev` | debug-skill, rtk (output token compression) |
| `tracking` | beads, dolt, beads-mcp |
| `general` | desktop-commander, sequential-thinking |
| `coding` | pyreorder, python-ultimate, code-deduplication, uv |
| `docs` | project-docs, good-readme, visual-explainer, beautify-github-readme, scientific-figures, mermaid-diagrams |
| `rust` | rust-skills, rust-pyo3-bindings, rust-dsp-stack |

## Implementation

Both tasks call `.config/mise/scripts/install_dev_features.py`:

- Tools are installed with `mise use <tool>@latest` so they land in the
  project's `[tools]` block and their shims resolve.
- MCP servers are registered with `bun x add-mcp <command> -a <agent> -n <name>`.
- Skills are installed with `bun x skills add <repo> [-s <skill>] -a universal -y`.

The tasks are best-effort: a failing tool or server prints its command and moves
on rather than aborting the run. The manifests are pre-commit- and DOX-exempt
tooling data, not project configuration.
