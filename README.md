# ParseCraft

> Document intelligence: convert any document into typed structured chunks, with Markdown as a deterministic projection

<p align="center">
  <a href="#"><img alt="Python 3.13 | 3.14+" src="https://img.shields.io/badge/python-3.13%20%7C%203.14%2B-3776ab?logo=python"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-green.svg"></a>
  <a href="https://github.com/jr2804/parsecraft/actions"><img alt="CI" src="https://github.com/jr2804/parsecraft/actions/workflows/ci.yml/badge.svg"></a>
</p>

ParseCraft turns a document into a typed intermediate representation (IR) of
structured chunks. Backends analyze a source and convert bounded page slices
into IR pages; a dependency-free renderer projects the IR to deterministic
Markdown. The IR is the source of truth — every other output is a projection
of it.

The project is at Phase 0. The IR schema, the backend protocol and registry,
and the CLI are implemented and covered by tests. Document adapters, the config
engine, and concrete OCR/VLM backends arrive in later phases.

| Component | State |
| --------- | ----- |
| IR schema + Markdown projection (`parsecraft.ir`) | Implemented |
| Backend protocol + registry (`parsecraft.backends`) | Implemented |
| CLI (`parsecraft`, `parsecraft backends`) | Implemented |
| Entry-point backend discovery | Implemented |
| Document adapters, config engine, OCR/VLM backends | Planned |
| PyPI distribution | Not published |

## Requirements

- Python 3.13 (GIL only — the free-threaded `3.13t` build is not supported)
- Python 3.14 or newer, including free-threaded builds (`3.14t`)
- [uv](https://docs.astral.sh/uv/) for dependency management
- [mise](https://mise.jdx.dev/) for the project task runner

## Quick Start

```bash
git clone https://github.com/jr2804/parsecraft.git
cd parsecraft
mise dev            # uv sync -U --dev --all-extras --all-groups
mise test           # pytest with the 100% coverage gate
uv run parsecraft backends
```

`mise dev` installs dependencies only. `mise test`, `mise lint`, and
`mise format` run the quality gates.

## CLI

| Command | Description |
| ------- | ----------- |
| `parsecraft` | Show help (`no_args_is_help`) |
| `parsecraft default` | Print the welcome message |
| `parsecraft backends` | List registered backends; prints load warnings to stderr |
| `parsecraft backends --json` | Emit the backend descriptors as JSON |
| `parsecraft --version` (`-v`) | Print the package version |

```bash
uv run parsecraft backends
# No backends registered.        (fresh checkout)

# After installing a backend:
# example-echo             cpu              text
# warning: backend 'broken' failed to load: ...   (stderr)

uv run parsecraft --version
```

| Environment Variable | Description |
| -------------------- | ----------- |
| `PARSECRAFT_JSON` | Default for the `--json` flag on `parsecraft backends` |

## Project Structure

```text
parsecraft/
├── .config/mise/               # mise task definitions
├── .github/workflows/          # CI, docs, and release workflows
├── docs/                       # Zensical documentation site
│   ├── adr/                    # Architecture decision records
│   └── reference/              # API + CLI reference
├── examples/
│   └── third_party_backend/    # Working entry-point backend
├── scripts/
│   └── gen_credits.py          # Credits generator for the docs site
├── src/parsecraft/
│   ├── __init__.py
│   ├── __about__.py            # Package metadata
│   ├── backends/               # Protocol, registry, errors
│   ├── cli/                    # Typer app, commands, args
│   ├── ir/                     # IR models + Markdown projection
│   └── py.typed
├── tests/                      # pytest suite (100% coverage gate)
├── pyproject.toml              # uv + hatch + pytest config
├── ruff.toml                   # Linter + formatter config
├── ty.toml                     # Type checker config
└── zensical.toml               # Docs site configuration
```

## Development

```bash
mise test       # pytest with the 100% coverage gate
mise lint       # ruff check
mise typecheck  # ty check src/ tests/
mise spell      # codespell
mise format     # ruff format + isort + clean-sort
mise all        # test + lint + spell + format + format-md + docs
```

Pre-commit hooks run a subset of these on every commit:

```bash
pre-commit install
pre-commit run --all-files
```

The docs site builds with `mise docs` (`uv run zensical build`). Serve it live
with `uv run --link-mode=copy zensical serve`.

## CI/CD

| Workflow | Triggers | Jobs |
| -------- | -------- | ---- |
| **CI** (`ci.yml`) | push to main, PR to main | quality (`mise lint` + `mise spell`), test matrix (ubuntu/macos/windows: 3.13, 3.14, 3.14t; ubuntu: 3.15-dev, allowed to fail), docs build |
| **Release** (`release.yml`) | push to main, manual | compute the next CalVer version, tag it, build the package, create a GitHub release, publish to PyPI, deploy the docs site to GitHub Pages |

## Documentation

Full documentation: <https://jr2804.github.io/parsecraft/>

- [Getting started](https://jr2804.github.io/parsecraft/getting-started/quickstart/)
- [Architecture](https://jr2804.github.io/parsecraft/architecture/overview/)
- [Write a backend](https://jr2804.github.io/parsecraft/guides/backend-authoring/)
- [CLI reference](https://jr2804.github.io/parsecraft/reference/cli/)
- [API reference](https://jr2804.github.io/parsecraft/reference/api/)

## AI Dev-Features

The project ships optional AI-agent tooling. After `mise dev`, install with:

```bash
mise run add-mcp-servers <agent>   # register MCP servers (claude, codex, gemini, ...)
mise run add-skills                # install agent skills
```

Enabled dev-features are listed in `.config/mise/conf.d/mcp.toml` and
`.config/mise/conf.d/skills.toml`.

## Tech Stack

| Layer | Tool | Purpose |
| ----- | ---- | ------- |
| Package manager | [uv](https://docs.astral.sh/uv/) | Fast installs, deterministic lockfile |
| Task runner | [mise](https://mise.jdx.dev/) | DAG-based tasks, tool version management |
| Linter + formatter | [ruff](https://docs.astral.sh/ruff/) | Single-binary code quality |
| Type checker | [ty](https://github.com/google/ty) | Strict type checking |
| Testing | [pytest](https://pytest.org/) | Test framework with 100% coverage gate |
| Spell check | [codespell](https://github.com/codespell-project/codespell) | Code and doc spell checking |
| Documentation | [Zensical](https://github.com/zensical/zensical) | MkDocs Material with executable examples |
| Versioning | [uv-dynamic-versioning](https://github.com/ninoseki/uv-dynamic-versioning) | Git tag-based versioning |
| Hooks | [pre-commit](https://pre-commit.com/) | Automated quality gate |
| CI/CD | GitHub Actions | Test matrix, docs, PyPI release |

## License

MIT — see [LICENSE](LICENSE) for details.

---

Generated from [copier-uv-plus](https://codeberg.org/jr2804/copier-uv-plus).
