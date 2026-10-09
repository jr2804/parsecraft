---
title: CI and CD
---

## Continuous integration

`.github/workflows/ci.yml` runs on pushes to `main` and on pull requests to
`main`. The `quality` job gates `tests` and `docs`; the two extras-resolution
jobs run in parallel:

| Job | Runner | Runs |
| --- | ------ | ---- |
| `extras` | ubuntu, windows | `uv sync -U --all-extras --all-groups --all-packages --dry-run` on 3.13 (root `AGENTS.md` rule 10) |
| `extras-availability` | ubuntu, macos, windows | the same assertion on 3.14 and 3.14t; **reporting only** (`continue-on-error`) |
| `quality` | ubuntu | `mise lint` + `mise spell` + `mise format-check` |
| `tests` | ubuntu, macos, windows | `mise test` (100% coverage gate) |
| `docs` | ubuntu | `mise docs` |

`extras` verifies rule 10 (every optional extra must resolve jointly) on
CPython 3.13 alone. `extras-availability` extends the same assertion to the
3.14 and 3.14t (free-threaded) cells on all three OSes, keeping `--dry-run`
so no cell installs. It is **reporting, never gating**: job-level
`continue-on-error`, because a gating all-extras job on 3.14t fails on
`mineru` today — `onnxruntime` publishes no free-threaded macOS/Windows
wheels and has no sdist. Every cell writes a step summary naming that known
cause and the re-entry condition, and failing cells raise a notice annotation,
so a red check is read rather than tuned out.

**Promotion trigger:** when `onnxruntime` publishes free-threaded
macOS/Windows wheels, drop `continue-on-error` from `extras-availability` and
it becomes the gate root `AGENTS.md` rule 10 always claimed to have.

The `quality` job's `format-check` is the CI half of the local `mise all`
gate: it verifies that the committed tree is already canonical (`pyreorder
check`, `rumdl check`, `ruff format --check`, import order) and never rewrites
anything. The formatters themselves run only locally — a gate that only
reformats cannot detect drift, it just leaves it in the working tree for the
next run to rewrite again.

The test matrix:

| Python | OS | Notes |
| ------ | -- | ----- |
| 3.13 | ubuntu, macos, windows | GIL-enabled builds |
| 3.14 | ubuntu, macos, windows | |
| 3.15-dev | ubuntu | `continue-on-error` — allowed to fail |

Free-threaded (`3.14t`) cells are **suspended** (ADR-0001 amendment, 2026-10-09):
heavy runtimes such as `onnxruntime` ship no free-threaded macOS/Windows wheels
and no sdist, so an extra cannot resolve there. They return unchanged once those
wheels exist.

The `tests` job sets `MISE_AUTO_INSTALL=0`. The dev toolset is not needed by that
job (only uv plus the selected interpreter, and some tools lack wheels on the
newest interpreters).
The free-threaded runtime floor and CI matrix are recorded in
[ADR-0001](../adr/0001-phase-0-decisions.md).

## Release

`.github/workflows/release.yml` runs on every push to `main` (and manually via
`workflow_dispatch`):

1. Compute the next CalVer version `YYYY.M.N` from existing tags; reuse the tag
   already pointing at `HEAD` when present.
2. Create and push the annotated tag.
3. Build the wheel and sdist (`uv build`) and create a GitHub release with
   generated notes.
4. Publish to PyPI with trusted publishing (`pypa/gh-action-pypi-publish`,
   OIDC — no stored API token), skipping files that already exist.
5. Build the docs site and deploy it to GitHub Pages.

Packaging reads the version through `uv-dynamic-versioning`; `[tool.uv.dynamic-versioning]`
declares the bare CalVer tag pattern because the default expects a `v` prefix.
The distribution version and `parsecraft --version` both come from the installed
package metadata.

Release and distribution decisions are recorded in
[ADR-0002](../adr/0002-release-and-distribution.md).
