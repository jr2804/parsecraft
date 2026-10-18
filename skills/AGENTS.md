# AGENTS.md — skills/

## Purpose

Agent-facing skills that teach assistants how to use ParseCraft itself — the
product from the outside, not internal repo workflow.

## Ownership

- `parsecraft/SKILL.md` — the `parsecraft` CLI skill (entry point).
- `parsecraft/references/` — long-form reference material the skill links to
  (`backends.md`, `json-output.md`).

## Local Contracts

- **Location**: product skills live in `skills/`; internal agent-workflow skills
  (e.g. beads) stay under `.agents/skills/`. Do not move a skill between them.
- **Facts come from source**: every flag, exit code, extra, backend name, env
  var, and schema field written into a skill must be verified against the
  installed CLI (`parsecraft … --help`, live runs) or `src/parsecraft/` /
  `docs/` — never from memory. Prefer `pyproject.toml` and the CLI over docs
  prose where they disagree; do not copy retired names (e.g. the removed `auto`
  meta-extra) into skill text.
- **Skills rot with the CLI**: a change to convert flags, extras, backend
  registration, or the JSON schema updates `skills/parsecraft/` in the same
  change set.
- **Skill-creator conventions**: `SKILL.md` under 300 lines, frontmatter limited
  to `name` + `description`, references one level deep, files over 100 lines get
  a TOC, every bundled file reachable from `SKILL.md`.

## Work Guidance

Write skills in imperative voice, task-based structure, self-contained (a
reader may not have the repo checkout).

## Verification

Before landing:

```bash
uvx --from skills-ref agentskills validate skills/parsecraft
```

Both validators exit 0 (`agentskills validate`, skill-creator
`scripts/quick_validate.py`). Then a green repo-wide `mise all`.

## Child DOX Index

None — leaf boundary.
