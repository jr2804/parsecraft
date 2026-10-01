---
title: Tech stack
---

## Runtime dependencies

These install with `pip install parsecraft` (`pyproject.toml` `[project] dependencies`).

| Package | Role |
| ------- | ---- |
| [markdown-it-py](https://github.com/executablebooks/markdown-it-py) | CommonMark parsing for the Markdown input adapter (lazy import) |
| [pydantic](https://docs.pydantic.dev/) | IR schema and validation |
| [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) | Configuration schema base |
| [platformdirs](https://github.com/tox-dev/platformdirs) | Platform-appropriate config and cache directories |
| [tomlkit](https://github.com/python-poetry/tomlkit) | Style-preserving TOML read and write |
| [typer](https://typer.tiangolo.com/) | CLI framework |

### Optional extras

| Extra | Package | Role |
| ----- | ------- | ---- |
| `download` | [huggingface-hub](https://huggingface.co/docs/huggingface_hub) | Pinned model-asset downloads (lazy import) |

Core is import-clean offline: no network or heavy runtime is imported at import
time, and no model weights, CUDA, or OCR stacks ship with a base install.
Runtime floor is `>=3.13` with classifiers for 3.13 and 3.14.

## Development tooling

Dev tooling is not part of the distribution.

| Tool | Installed by | Role |
| ---- | ------------ | ---- |
| [uv](https://docs.astral.sh/uv/) | manually | Dependency and environment manager |
| [mise](https://mise.jdx.dev/) | manually | Task runner and pinned tool versions |
| [hatchling](https://hatch.pypa.io/) + [uv-dynamic-versioning](https://github.com/ninoseki/uv-dynamic-versioning) | build | Build backend and CalVer versioning |
| [pytest](https://pytest.org/) + [pytest-cov](https://pytest-cov.readthedocs.io/) | dev group | Tests and the 100% coverage gate |
| [ruff](https://docs.astral.sh/ruff/) | mise | Linter and formatter |
| [ty](https://github.com/google/ty) | mise | Type checker |
| [codespell](https://github.com/codespell-project/codespell) | mise | Spell checker |
| [rumdl](https://github.com/rvben/rumdl) | mise | Markdown linter and formatter |
| [pyreorder](https://github.com/jr2804/pyreorder) | mise | Structural sorter |
| [pre-commit](https://pre-commit.com/) | manually | Git hook runner |
| [Zensical](https://github.com/zensical/zensical) + [mkdocstrings](https://mkdocstrings.github.io/) + [markdown-exec](https://github.com/pawamoy/markdown-exec) + [markdown-callouts](https://github.com/oprypin/markdown-callouts) | dev group | Documentation site and API reference |
| [ghp-import](https://github.com/cpburnz/python-ghp-import) | dev group | Template-provided Pages helper |

Dev tool tasks are listed in [Development setup](setup.md). The optional agent
tooling built on top of mise is described in
[AI dev features](ai-dev-features.md).
