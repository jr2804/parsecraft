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
| `quality` | ubuntu | `mise lint-check` + `mise typecheck` + `mise spell` + `mise format-check` + `mise actionlint` |
| `tests` | ubuntu, macos, windows | `mise test` (100% coverage gate) |
| `docs` | ubuntu | `mise docs` |

`extras` verifies rule 10 (every optional extra must resolve jointly) on
CPython 3.13 alone. `extras-availability` extends the same assertion to the
3.14 and 3.14t (free-threaded) cells on all three OSes, keeping `--dry-run`
so no cell installs. It is **reporting, never gating**: job-level
`continue-on-error`, because a gating all-extras job on 3.14t still fails
where any extra pulls `onnxruntime` — it publishes no free-threaded
macOS/Windows wheels and has no sdist. (The former `mineru` trigger for this
was removed with the package on 2026-10-26.) Every cell writes a step summary
naming that known cause and the re-entry condition, and failing cells raise a
notice annotation, so a red check is read rather than tuned out.

**Promotion trigger:** when every remaining `onnxruntime` pull is gone or it
publishes free-threaded macOS/Windows wheels, drop `continue-on-error` from
`extras-availability` and it becomes the gate root `AGENTS.md` rule 10 always
claimed to have.

The `quality` job's `format-check` is the CI half of the local `mise all`
gate: it verifies that the committed tree is already canonical (`pyreorder
check`, `rumdl check`, `ruff format --check`, import order) and never rewrites
anything. The formatters themselves run only locally — a gate that only
reformats cannot detect drift, it just leaves it in the working tree for the
next run to rewrite again.

**No CI step runs a fixer.** The same rule applies to the linter, which is why
the step is `mise lint-check` and not `mise lint`: `lint` is `ruff check --fix`,
so a finding was repaired on the runner and the step exited `0`. Measured
2026-11-01 — a tree with an unused import plus a canonical block passed the
quality job while the local `mise all` rejected it, because the local gate uses
`lint-check` (pc-9p6.2) and CI had kept the original `mise lint`. A gate that
repairs what it gates can only fail on findings it cannot fix, and it validates
a tree the contributor never had. `tests/test_ci_gate_tasks.py` enforces this
mechanically: it loads each `mise <task>` a workflow invokes, reads that task's
commands from `.config/mise/`, and fails if any of them rewrites files — so a
future task cannot reintroduce the inversion by name alone.

`mise typecheck` is part of the local gate but **not yet a CI step** (pc-180).
Adding it is not a one-line job: ty's verdict depends on which extras are
installed, so the same commit types clean in the `mise dev` environment and dirty
in an all-extras one — the `# ty: ignore[unresolved-import]` markers on the heavy
imports are load-bearing there and invert to `unused-ignore-comment` once the
extra is present. The job must pin the environment it types against and say so.

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

Workflow hardening (enforced by `mise actionlint`, config in
`.github/jactionlint.yaml`): every `uses:` is pinned to a full-length commit SHA
(bumped deliberately, never by hand-edited tag), every job carries a
`timeout-minutes` bound, workflow permissions are minimal (`contents: read` in
ci.yml, per-job elevation in release.yml only where a job publishes), and
checkouts set `persist-credentials: false` unless the job pushes via git.

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

Between steps 3 and 4 the release body is rewritten: an optional AI summary,
a collapsible full commit list, and the compare link. That step is **opt-in by
operator configuration and off by default** — there is deliberately no built-in
model, because an unset variable means "keep this off", not "pick a model for
me". Two knobs, both required:

| Knob     | Kind     | Meaning                                |
| -------- | -------- | -------------------------------------- |
| `OPENROUTER_MODEL` | repo variable | model id to call; unset skips the step |
| `OPENROUTER_API_KEY` | secret | credential; the step notices and exits 0 without it |

The step is `continue-on-error`, so a router failure never fails a release. Two
outcomes look similar from the outside and are easy to confuse:

- **Step fails (or the model is rejected):** the body stays exactly what
  `generate_release_notes` produced — usually just
  `**Full Changelog**: …/compare/<previous>...<next>` with no per-commit
  detail, because this repo's commits are grouped into releases rather than
  listed. The release itself is intact; check
  `gh run view <id> --log | Select-String "AI release notes"` for the reason.
- **Model answers but the summary is unusable** (under 200 characters, or no
  heading and no list item — a moderation verdict like `User Safety: safe`
  arrives as a well-formed HTTP 200): the step prints a warning, emits nothing,
  and the generated changelog again stands.

A model id can disappear from a router's free tier without notice (upstreams
retire `:free` slugs and keep the paid one), which is the failure this step
reports most often. The fix is an operator run —
`gh variable set OPENROUTER_MODEL -b "<vendor>/<model>"` against a slug from
`https://openrouter.ai/api/v1/models` — never a workflow edit.

**Being listed is not being callable.** Three failures arrive as a slug that
looks fine in `/api/v1/models` and still refuses this step's request, all
observed on 2026.11.1 while replacing a retired slug:

| response | meaning |
| -------- | ------- |
| `403` `… is only available on agentic harnesses` | the free endpoint is gated to OpenRouter's app integrations; a workflow call never qualifies |
| `429` `temporarily rate-limited upstream` | the provider's own limit, shared anonymously — free capacity, not a config error |
| `503` `no upstreams available` | the provider was absent at call time |

So probe a candidate before setting the variable, with the request shape this
step uses (a single user message, no system prompt, no tools) and the same
quality guard — at least 200 characters carrying a heading or a list item:

```powershell
# OPENROUTER_API_KEY in the environment; MODEL from /api/v1/models
$prompt = 'Write release notes in Markdown with a heading and a bullet list from: - fix a bug'
$body = @{ model = $env:MODEL; messages = @(@{ role = 'user'; content = $prompt }) } |
  ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri https://openrouter.ai/api/v1/chat/completions -Method Post `
  -Headers @{ Authorization = "Bearer $env:OPENROUTER_API_KEY" } `
  -ContentType application/json -Body $body |
  ForEach-Object { $_.choices[0].message.content }
```

A slug that answers there answers in the workflow. `nvidia/nemotron-3-ultra-550b-a55b:free`
is the slug in use as of 2026.11.1 for exactly this reason: it was the first
candidate that returned usable notes for a plain call.

Packaging reads the version through `uv-dynamic-versioning`; `[tool.uv.dynamic-versioning]`
declares the bare CalVer tag pattern because the default expects a `v` prefix.
The distribution version and `parsecraft --version` both come from the installed
package metadata.

Release and distribution decisions are recorded in
[ADR-0002](../adr/0002-release-and-distribution.md).
