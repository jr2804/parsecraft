# ParseCraft

> Document intelligence: align existing converters, parsers, and OCR/VLM models behind one workflow and one typed output format

<p align="center">
  <a href="#"><img alt="Python 3.13 | 3.14+" src="https://img.shields.io/badge/python-3.13%20%7C%203.14%2B-3776ab?logo=python"></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-green.svg"></a>
  <a href="https://github.com/jr2804/parsecraft/actions"><img alt="CI" src="https://github.com/jr2804/parsecraft/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://pypi.org/project/parsecraft/"><img alt="PyPI" src="https://img.shields.io/pypi/v/parsecraft.svg"></a>
</p>

ParseCraft is a thin aggregation and alignment layer for document intelligence.
Capable converters, parsers, and OCR/VLM models already exist, but each emits a
different shape — and many ship demo-grade wrapper code pinned to an outdated
Python version and dependency set. ParseCraft keeps every tool isolated behind a
backend, translates its output into a single typed intermediate representation
(IR), and projects that IR to deterministic Markdown. You get one workflow and
one output format, while every heavy runtime stays optional.

The IR is the source of truth. Backends read a document and produce typed pages
and chunks; every rendering — Markdown today, HTML/JSON/consumer trees later —
is a projection of that same IR, never parsed back into state.

## Requirements

- Python 3.13 (GIL only — the free-threaded `3.13t` build is not supported)
- Python 3.14 or newer, including free-threaded builds (`3.14t`)
- [uv](https://docs.astral.sh/uv/) (recommended) or pip to install the package

## Install and Quick Start

Install the released package and list the available backends:

```bash
uv add parsecraft      # or: pip install parsecraft
uv run parsecraft backends
```

First release: `2026.9.2`. The current version is on
[PyPI](https://pypi.org/project/parsecraft/).

Building from a source checkout, running the test suite, and working on the
codebase are covered in the [development setup](https://jr2804.github.io/parsecraft/development/setup/).

## CLI

| Command | Description |
| ------- | ----------- |
| `parsecraft backends` | List registered backends; load warnings go to stderr |
| `parsecraft backends --json` | Emit backend descriptors as JSON |
| `parsecraft config check` | Validate the effective configuration |
| `parsecraft config show` | Show the resolved configuration with provenance |
| `parsecraft --version` (`-v`) | Print the package version |

```bash
uv run parsecraft backends
# No backends registered.        (no third-party backend installed yet)
```

See the [CLI reference](https://jr2804.github.io/parsecraft/reference/cli/) for
every command, flag, exit code, and environment variable.

## How it works

```text
source document → backend (analyze / convert) → typed IR → Markdown projection
```

- **Backends are the extension point.** A third party ships a `BackendFactory`
  under the `parsecraft.backends` entry-point group; the core package does not
  change to add one.
- **One typed IR.** `DocumentResult`, `PageResult`, and `StructuredChunk` move
  between layers, and Markdown is a deterministic, dependency-free projection.
- **Heavy runtimes stay optional.** Model weights, CUDA, and OCR/VLM stacks load
  only inside a backend factory, so importing ParseCraft stays offline-clean.
- **Failures are typed.** A failed pass records a `PassFailure` rather than
  silently dropping a page.

Continue with the [architecture overview](https://jr2804.github.io/parsecraft/architecture/overview/),
the [IR model](https://jr2804.github.io/parsecraft/architecture/ir/), the
[backend catalog](https://jr2804.github.io/parsecraft/reference/backends/), and
the [backend authoring guide](https://jr2804.github.io/parsecraft/guides/backend-authoring/).

## Documentation

- [Installation](https://jr2804.github.io/parsecraft/getting-started/installation/)
- [Quickstart](https://jr2804.github.io/parsecraft/getting-started/quickstart/)
- [Architecture](https://jr2804.github.io/parsecraft/architecture/overview/)
- [Backends](https://jr2804.github.io/parsecraft/architecture/backends/)
- [Write a backend](https://jr2804.github.io/parsecraft/guides/backend-authoring/)
- [Backend catalog](https://jr2804.github.io/parsecraft/reference/backends/)
- [CLI reference](https://jr2804.github.io/parsecraft/reference/cli/)
- [API reference](https://jr2804.github.io/parsecraft/reference/api/)
- [Development setup](https://jr2804.github.io/parsecraft/development/setup/)

## License

MIT — see [LICENSE](LICENSE) for details.
