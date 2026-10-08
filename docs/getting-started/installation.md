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

## GPU torch

OCR backends need `torch`, and the PyPI Windows/Linux wheels are **CPU builds**
— so an OCR backend on a CUDA host would silently run on the CPU. When `torch`
or `torchvision` is resolved from this project (a checkout, or a downstream
project that copies the index block from `pyproject.toml`), uv therefore pulls
them from the **PyTorch cu128 index** automatically:

| Platform | `torch` source | Device |
| -------- | -------------- | ------ |
| Windows, Linux | `download.pytorch.org/whl/cu128` | CUDA (cu128; older GPUs may need a different CUDA — override the index in your own `pyproject.toml`) |
| macOS | PyPI (index excluded by marker) | CPU + MPS |

The project's `torch` version *ranges* are unchanged by this — the index only
provides the CUDA variant. `uv tool install`/`uvx` installs of the CLI carry no
torch at all (torch arrives with the OCR extras), so plain CLI installs are
unaffected.

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

Optional features live in extras, and one of them is a shortcut:

```bash
pip install "parsecraft[auto]"        # the judge providers + the classifier provider
parsecraft judges                     # provider tokens, credentials, extras
```

`auto` is a meta-extra that pulls in `parsecraft[systemone]` and
`parsecraft[pdf-inspector]` for the `--judge` and `--classifier` flags. It does
not include `pdf`/`pdf-lite` or any OCR backend, so pick those separately when
you need PDF extraction. Judge credentials come from the environment
(`TYPESAFE_API_KEY`, `OPENCODE_API_KEY`) — see
[Backends](../reference/backends.md) for the full extras list and
[Steer the auto-backend selector](../guides/auto-backend-selector.md) for the
flags themselves.

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
