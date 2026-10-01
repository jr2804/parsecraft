---
title: Installation
---

## Requirements

| Requirement | Version | Notes |
| ----------- | ------- | ----- |
| Python | 3.13 or 3.14+ | `requires-python = ">=3.13"` — see Supported runtimes |
| [uv](https://docs.astral.sh/uv/) | current | Dependency and environment manager |
| [mise](https://mise.jdx.dev/) | current | Runs the project tasks and tool versions |

## Supported runtimes

| Runtime | Support |
| ------- | ------- |
| CPython 3.13 | GIL-enabled build only; the free-threaded `3.13t` build is not supported |
| CPython 3.14 | Supported, including the free-threaded `3.14t` build (CI: ubuntu, macos, windows) |
| CPython 3.15-dev | Best-effort; CI allows this job to fail |

## From PyPI

First release: `2026.9.2`. Add the dependency with uv:

```bash
uv add parsecraft
uv run parsecraft backends
```

Or with pip:

```bash
pip install parsecraft
parsecraft backends
```

Current version on [PyPI](https://pypi.org/project/parsecraft/).

## From source

Use a checkout when contributing or testing unreleased changes. Clone and sync:

```bash
git clone https://github.com/jr2804/parsecraft.git
cd parsecraft
uv sync --dev
uv run parsecraft backends
```

Or use mise, which also installs the pinned tool versions:

```bash
git clone https://github.com/jr2804/parsecraft.git
cd parsecraft
mise dev      # uv sync -U --dev --all-groups --extra download --extra web --extra pdf-lite
uv run parsecraft backends
```

`mise dev` writes the virtual environment created by uv. Activate it with
`source .venv/bin/activate` (POSIX) or `.venv\Scripts\activate` (Windows) if
you prefer `parsecraft` over `uv run parsecraft`.

## Verify the install

```bash
uv run parsecraft --version
uv run parsecraft backends
```

`parsecraft backends` prints `No backends registered.` on a fresh checkout —
no third-party backend is installed by default. Install the example backend to
see discovery working:

```bash
uv pip install examples/third_party_backend
uv run parsecraft backends
# example-echo             cpu              text
```

## Development install

The dev group ships pytest, Zensical, mkdocstrings, and the coverage tooling.

```bash
mise test       # pytest with the 100% coverage gate
mise lint       # ruff + ty + codespell
mise all        # test + lint + spell + format + format-md + docs
```

See [Contributing](../contributing.md) for the full workflow.
