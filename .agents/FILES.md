# AGENTS.md — FILES

Single source of truth for paths, config keys, and naming conventions.
Kept compact — agents hallucinate less when they know where definitions live.

## Pattern

- One file owns each class of definition (paths, config defaults, enums).
- Import from that file. Never hard-code values in other modules.
- Variables that address files get `_file` suffix; directories get `_dir`.

## Project-specific sources of truth

| What                   | Where                                                           | Key names                                       |
| ---------------------- | --------------------------------------------------------------- | ----------------------------------------------- |
| Package source root    | `src/parsecraft/`  | `__init__.py`, `__about__.py` (version)         |
| CLI entry points       | `src/parsecraft/cli/`            | `app.py` (Typer app), `args.py`, `commands.py`  |
| Test suite             | `tests/`                                                        | `test_*.py`, `conftest.py`                      |
| Issue tracker (beads)  | `.beads/`                                                       | `bd` CLI, issue prefix `pc`                     |
| Project metadata       | `pyproject.toml`                                                | `[project]`, `[tool.pytest.ini_options]`        |
| Linter/formatter       | `ruff.toml`                                                     | Ruff rule selection, line length                |
| Type checker           | `ty.toml`                                                       | ty strictness                                   |
| Markdown linter        | `.config/rumdl.toml`                                            | `line-length`, `flavor`, disabled rules         |
| mise tasks             | `.config/mise/config.toml` + `.config/mise/conf.d/*.toml`       | `[tasks.dev]`, `[tasks.test]`, ...              |
| Copier answers         | `.copier-answers.yml`                                           | Template version + answers (regenerated)        |
| Pre-commit config      | `.pre-commit-config.yaml`                                       | Hook list                                       |
| MCP server config      | `.config/mise/conf.d/mcp.toml`                                  | `[tasks.add-mcp-servers]`, tool list            |
| Skills install task    | `.config/mise/conf.d/skills.toml`                               | `[tasks.add-skills]`, skill list                |

## Naming conventions

- **Repository name** (`project_slug`): kebab-case (`my-project`)
- **Python package** (`package_slug`): snake_case (`my_project`)
- **Layout**: `src/parsecraft/`
- **Test files**: `test_<module>.py`
- **Template files** (in source template): `filename.ext.jinja` — the `.jinja`
  suffix is stripped on generation
