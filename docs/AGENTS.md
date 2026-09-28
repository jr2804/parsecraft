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
  `tests/test_benchmark_report.py`.
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
- **Dated deferrals.** A measurement or run that was deliberately not performed
  is recorded as a deferral with a date, the reason (cost/time or scope, never
  "failed"), and the exact command to run it later — never left as silent prose
  in a results page.
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
