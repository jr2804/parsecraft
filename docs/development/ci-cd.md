---
title: CI and CD
---

## Continuous integration

`.github/workflows/ci.yml` runs on pushes to `main` and on pull requests to
`main`. Three jobs fan out from a `quality` gate:

| Job | Runner | Runs |
| --- | ------ | ---- |
| `quality` | ubuntu | `mise lint` + `mise spell` + `mise format-check` |
| `tests` | ubuntu, macos, windows | `mise test` (100% coverage gate) |
| `docs` | ubuntu | `mise docs` |

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
