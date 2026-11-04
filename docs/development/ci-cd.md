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
| `extras-availability` | ubuntu, windows | every extra resolves on 3.14 and 3.14t; a recorded upstream wheel gap passes, anything else fails |
| `quality` | ubuntu | `mise lint-check` + `mise typecheck` + `mise spell` + `mise format-check` + `mise actionlint` |
| `tests` | ubuntu, windows | `mise test` (100% coverage gate) |
| `docs` | ubuntu | `mise docs` |

The macOS legs are not in this workflow at all — they live in
`ci-macos.yml`, see [the macOS leg](#the-macos-leg-has-its-own-workflow-it-cannot-hold-up-a-release).

`extras` verifies rule 10 (every optional extra must resolve jointly) on
CPython 3.13 alone. `extras-availability` extends the same assertion to the
3.14 and 3.14t (free-threaded) cells on all three OSes, and it **gates**: the
step runs `scripts/extras_availability.py`, which classifies the resolver
failure instead of tolerating all of them.

A missing artefact whose package is recorded in `KNOWN_GAPS` (with a reason
and a re-entry condition) is a notice and the cell passes — today that is
`onnxruntime` (no free-threaded macOS/Windows wheel, no sdist, reached through
`pymupdf`), plus `vllm`, `nvidia-cudnn-frontend` and `pynvvideocodec` on macOS
and free-threaded Linux. Anything else — a missing wheel nobody has recorded,
an unsatisfiable graph, a build failure — **fails the cell**, so a real rule 10
violation can no longer hide behind a familiar name. Tolerance is declared in
one list, never assumed by `continue-on-error` (which made every cause look
identical and trained readers to ignore the check).

Adding a gap means adding a `KnownGap` with its re-entry condition; the test
suite rejects an entry without one. The job runs the script through
`uv run --no-project` so the cell's own project resolution cannot kill the
step before it reports.

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

`mise typecheck` runs in the `quality` job, **reported and never gating**
(user ruling 2026-11-04): ty's verdict depends on which extras are installed, so
a type finding must not block a release. The verdict is printed on every run,
and `mise all` still type-checks locally as a hard gate. (`extras-availability`
is a different thing and does gate: its tolerance is declared per recorded gap
rather than assumed, so a new blocker fails the cell.)

The job pins the environment it types against, and says so: the canonical dev
env that `mise dev` produced above (`uv sync --dev --all-groups --extra web`,
no heavy extras), never an all-extras one. The same commit types clean in the
dev environment and dirty in an all-extras one — the
`# ty: ignore[unresolved-import]` markers on the heavy imports are load-bearing
there and invert to `unused-ignore-comment` once the extra is present. The task
itself pins `VIRTUAL_ENV` to the project's venv, so the check is hermetic.

The test matrix:

| Python | OS | Notes |
| ------ | -- | ----- |
| 3.13 | ubuntu, windows | GIL-enabled builds |
| 3.14 | ubuntu, windows | |
| 3.15-dev | ubuntu | `continue-on-error` — allowed to fail |

The macOS cells (3.13, 3.14) and the macOS `extras-availability` cells
(3.14, 3.14t) live in `ci-macos.yml` — see below.

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

### The macOS leg has its own workflow: it cannot hold up a release

A hosted macOS runner can be unavailable for an entire run: GitHub never hands
the cell to a runner and cancels it. The cell's own annotation says it —
*"The job was not acquired by Runner of type hosted even after multiple
attempts"*, plus *"Due to capacity constraints, jobs targeting macOS arm64
runners may experience longer queue times"* — and the run turns red although
no macOS code ever ran. Observed 2026-11-04 on push `cd37a0a`: four macOS
cells queued for 15 minutes with no runner and were cancelled, while every cell
that actually ran was green. The same wall returned on a full re-run of the
workflow and on every single-cell re-run.

**A macOS cell that times out (or never gets a runner) must not block the
release** (user ruling 2026-11-04). That could not be fixed inside `ci.yml`:
a job that is still waiting for a runner keeps the RUN open, so the release —
which waits for the ci run to complete — would wait for the macOS leg either
way. Keeping it in the same workflow would also leave the run's conclusion at
the mercy of a cell that never ran.

So the macOS legs live in their own workflow, `.github/workflows/ci-macos.yml`:
the two test cells and the two free-threaded `extras-availability` cells. The
release is triggered by `ci` and never by `ci-macos`, so nothing macOS can do
reaches it — not a timeout, not a capacity cancellation, and not a macOS
regression. **A macOS regression does not block a release either**: it is
reported on the commit (the `ci-macos` run is red) and the release proceeds.
The macOS leg is a signal, not a gate.

The macOS leg is neither skipped nor hidden: it runs the same steps as the
gated job (`tests/test_ci_macos_leg.py` pins that), its run is on the commit,
and `ci-retry.yml` re-runs its capacity-cancelled cells exactly as it does for
the gated workflow's.

### Cancelled cells are retried, not treated as failures

A hosted macOS runner can be unavailable for an entire run: GitHub never hands
the cell to a runner and cancels it. The cell's own annotation says it —
*"The job was not acquired by Runner of type hosted even after multiple
attempts"*, plus *"Due to capacity constraints, jobs targeting macOS arm64
runners may experience longer queue times"* — and the run turns red although
no macOS code ever executed. Observed 2026-11-04 on push `cd37a0a`: four
macOS cells queued for 15 minutes with no runner and were cancelled, while
every cell that actually ran was green.

`.github/workflows/ci-retry.yml` re-runs exactly those cells once the run
has completed — for the `ci` run and for the `ci-macos` run alike:

| Cell state | Retried | Why |
| ---------- | ------- | --- |
| cancelled, **no runner was ever acquired** | yes | GitHub's runner capacity — the code never ran |
| cancelled after a runner was acquired | no | a timeout, or a human's decision; re-running it would undo that decision |
| failed | no | the code failed for a reason — the failure is the information |
| succeeded | no | it ran |

The retry can never turn a real breakage green: the failing cell stays
failed, so the run stays red. It only replaces the cells that never ran.

Exactly **one** cell is re-run per firing. GitHub accepts a cell re-run only
while the run sits in a terminal state, and one re-run puts it back in
progress — a second re-run in the same firing is refused with *"The workflow
run containing this job is already running"* (measured 2026-11-04). The next
firing happens when the run completes again, and takes the next cell.

Three guards bound the retries. The trigger accepts only a **push**-triggered
`ci` run on `main` — a pull-request run (including one from a fork whose
branch is named `main`) never reaches the re-run API, the same fork-pwn vector
`release.yml` closes — and only a run that did **not succeed**. The automatic
budget is finite (six re-runs per push, counted both on the run's attempt
counter and on this workflow's own firings for the push), so the retry can
never fight GitHub's runner capacity forever.

A run a **human interrupted** is left alone, and the two are told apart by the
cells, not by the run's conclusion: the capacity case cancels only cells that
never got a runner, while an interruption cancels a cell that had one. The
script therefore skips a run in which any cancelled cell carries a runner.

The run's conclusion is not the discriminator, and the reason is worth
recording: the capacity case concludes **`cancelled`**, not `failure` (measured
2026-11-04 — three macOS cells cancelled without a runner, every other cell
green, the run concluded `cancelled`). A `failure`-only guard would therefore
never fire in the very case it was written for.

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
