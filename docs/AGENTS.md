# AGENTS.md — docs/

Local DOX contract for the Zensical documentation site under `docs/`.

## Purpose

Own the published documentation site and the README it embeds: page structure,
navigation, and the doc build. Authoring standards live in shared tiers; this
file adds only what is local to `docs/`.

## Ownership

- `getting-started/` — tutorials (installation, quickstart).
- `architecture/` — explanation (overview, IR, backends).
- `guides/` — problem-oriented how-to guides.
- `development/` — setup, CI/CD, tech stack, and AI dev features.
- `reference/` — API fact source (`api.md`, mkdocstrings) and CLI facts
  (`cli.md`).
- `benchmarks/` — Phase 2 measurement reports: the authored page
  (`index.md`) plus committed harness artifacts (`benchmark.json`,
  `benchmark.md`, GPU-pass JSONs); regenerating artifacts is
  `parsecraft benchmark … -o docs/benchmarks/`, schema guarded by
  `tests/benchmark/test_report.py`.
- `adr/` — architecture decision records; **reserved for pc-1** — do not edit.
- Top-level pages — `index.md`, `credits.md`, `license.md`,
  `contributing.md`, `code_of_conduct.md`.
- `README.md` (repo root) is authored as a site source because `docs/index.md`
  embeds it — see Local Contracts.

Site config `zensical.toml` and `scripts/gen_credits.py` follow the
one-file-owns-each-definition pattern in `.agents/FILES.md`.

## Local Contracts

- **No content tabs.** `mise run format-md` runs rumdl with `flavor = "gfm"`,
  which reads `===` tab bodies as indented code and de-indents them, breaking
  pymdownx tab grouping. Present alternatives as a lead-in sentence plus a
  fenced block instead.
- **superfences `custom_fences`.** Any
  `[project.markdown_extensions.pymdownx.superfences]` entry in `zensical.toml`
  replaces Zensical's defaults. Re-declare the `mermaid` fence
  (`name = "mermaid"`, `class = "mermaid"`) and keep the `python` fence
  (`validator = "markdown_exec.validator"`,
  `format = "markdown_exec.formatter"`). Dropping the mermaid fence makes every
  diagram build as a plain code block, silently.
- **README embedding.** `docs/index.md` includes the repository README via
  `--8<-- "README.md"` (pymdownx.snippets, base path = repo root,
  `check_paths = true`). Keep the directive working. Links in README that must
  resolve on both GitHub and the site use the absolute Pages URL
  (`https://jr2804.github.io/parsecraft/...`); repo-relative paths break inside
  the embedded page.
- **Nav registration.** Every page under `docs/` must be listed in the `nav`
  of `zensical.toml`. A page without a nav entry is orphaned.
- **Markdown is a projection.** Doc pages document the IR; never treat rendered
  Markdown as a source of truth. See root AGENTS.md rule 4 and
  `src/parsecraft/ir/AGENTS.md`.
- **Facts come from source.** Re-derive commands, flags, env vars, paths, and
  types from `src/`, CLI `--help`, and tests. Never from memory. Do not
  document anything that does not exist.
- **Label unbuilt work.** Planned components are marked as planned or Phase N,
  never presented as implemented.
- **Consumers read the Upgrading table before the release body.** A change that
  requires work on the consumer's side — a removed module, an inert env var, a
  renamed export, a version that is wrong for one purpose — gets a dated row in
  `getting-started/installation.md` *Upgrading*, with the release it entered and
  what to do. Derive the row from the tag diff, not from memory, and state the
  tag you checked; the `.15`-only docling extra regression is the model case, a
  one-release defect that no release body would have named. Add a row when the
  change lands, while the reason is still in the commit message.
- **Dated deferrals.** A measurement or run that was deliberately not performed
  is recorded as a deferral with a date, the reason (cost/time or scope, never
  "failed"), and the exact command to run it later — never left as silent prose
  in a results page.
- **Install facts come from `pyproject.toml`.** Quote an extra, specifier, or
  platform marker once and point at `pyproject.toml` as the source of truth;
  never restate a different range in prose. Every optional extra must be jointly
  installable — `uv sync -U --all-extras --all-groups --all-packages` is the
  standing rule (root `AGENTS.md` rule 10).
- **Platform-availability facts come from the published wheel set.** A row in
  the availability table in `reference/backends.md` states what the registry
  actually publishes (ABI tags, platforms, sdist) — verify it against PyPI for
  the pinned version, and say in the commit message which versions you checked.
  Never infer it from a constraint solver or a prose summary: uv resolves
  against versions without modelling whether a wheel exists for the target
  platform, so a green `--all-extras` run is not evidence of installability.
- **Benchmark numbers are re-derived from the committed artifact.** Every figure
  in `benchmarks/index.md` and in a backend row that cites a measurement must
  come from the JSON under `docs/benchmarks/`, not from a run's console output,
  a sibling prose page, or the transcript of the session that ran it. Two
  failure modes this prevents, both observed: a caveat about a *mechanism*
  (weights can be fetched mid-run) gets written as a caveat about a
  *measurement* (these cells include a download), and a derived estimate
  (`≈ 145 s/page → ≈ 20 h`) survives a later run that contradicts it. When a
  correction lands, mark the old figure revised rather than deleting it — it may
  have been quoted onward.
- **`package_version` is install-time metadata, not the commit that ran.** On an
  editable install the local segment names the commit the package was *built*
  from; a run imports the working tree, which may be ahead of it. Any row whose
  reproduction depends on a specific tree must name that commit in prose beside
  the artifact. See *Provenance* in `benchmarks/index.md`.
- **ADRs.** Records live only in `docs/adr/`, in the project-docs skill's ADR
  format. Creation and edits are pc-1's call.

## Work Guidance

- Authoring rules (Diátaxis quadrants, tables, Mermaid, snippets, admonitions,
  prose): `.agents/skills/project-docs/SKILL.md` and its `references/`.
- README review: `.agents/skills/good-readme/SKILL.md`.
- Diagrams are Mermaid fences in the page; no generated assets are committed.
  Superfences must re-declare the mermaid fence — see Local Contracts.
- `docs/getting-started/quickstart.py` is executed in-page by markdown-exec.
  Run it with `uv run python docs/getting-started/quickstart.py` before editing.
- Keep pages scannable; split a page rather than nesting past `###`.

## Verification

```bash
mise run docs        # zensical build — must exit 0
mise run format-md   # rumdl fmt over docs/ — must exit 0
mise run spell       # codespell (currently src/ and tests/ only)
```

`mise run all` runs the composite gate. Docs prose is not covered by
`mise run spell`; rely on review and `mise run format-md`. A page is done only
when the build and rumdl pass.

## Child DOX Index

None. `docs/adr/` is owned by pc-1.
