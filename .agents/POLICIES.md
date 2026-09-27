# AGENTS.md — POLICIES

Always applicable. Boundaries, priorities, verification, checklist.

## Priorities

1. Correctness
2. Evidence
3. Safety
4. Minimal changes
5. Consistency
6. Performance

## Boundaries

- NEVER fabricate paths, commits, APIs, config keys, env vars, test results,
  or benchmark numbers. If you don't know, say so.
- NEVER guess at command names, flags, or paths. Read source or run `--help`.
- NEVER add secrets, API keys, or tokens to files. Use env vars.
- NEVER run destructive commands (`rm -rf`, `git reset --hard`,
  `git push --force`) without explicit confirmation.
- NEVER delete or move files without explicit instruction.
- NEVER create temp files in project root. Use dedicated temp dir.

## Change constraints

- Minimal, surgical edits. Preserve existing style.
- No new dependencies without explicit instruction.
- No unrelated refactoring while fixing a bug.

## Code quality

Universal defaults. Project-specific standards live in child AGENTS.md.

- **SRP** — one reason to change per module/function.
- **DRY** — check before adding. Extract on third occurrence (Rule of Three).
- **Redundant code** — remove dead branches, unreachable conditions, unused
  params/imports before finishing.
- **Duplication vs. abstraction** — prefer duplication over wrong premature
  abstraction.
- No new code-quality tooling by default — opt in per project.

## Completion checklist

- Change solves the stated problem
- Relevant validation ran (or gaps stated)
- No unintended side effects or secrets exposed

## DOX authoring (keep AGENTS.md lean)

AGENTS.md and `.agents/` files share limited context. Bloat and duplication
are the failure mode.

### Where a rule lives

A rule goes in the **highest (most general) tier that fully applies**:

- Applies to **all code in the project** → `.agents/POLICIES.md` (default).
- Meaningful **only inside one subtree** → that subtree's `AGENTS.md`.
- Just helps navigation → the parent's Child DOX Index, nothing else.

### Reference, don't restate

A rule appears in **exactly one file**. Other files use a **pointer line**:
`<topic> — see <file>`. Never copy the rule, rationale, or example.

### Rule first, rationale second, never third

A DOX line is the rule. If a rationale is costly to rediscover, add one
short clause. No multi-paragraph explanations or code pairs — those belong
in a skill or `docs/`, linked once.

### Content rules

- **No tree views.** Generate on demand with `rg --files | tree-cli --fromfile`.
- **No history in AGENTS.md files.** Git log has routine history. Record only
  decisions costly to rediscover — in `.agents/HISTORY.md` (or
  `.agents/history/` for overflow archives). See **Implementation Plans**
  below for archiving completed plans.
- **No TODO lists.** Use issue trackers or beads.

## Implementation Plans

- Plans, refactoring plans, and design docs go in `.agents/plans/<feature>/`
  — NOT under version control (gitignored).
- Name descriptively with phase-number prefixes so file order matches
  implementation order, e.g. `01-sub-feature.md`, `02-next-sub-feature.md`.
- NEVER commit plans to git. They are mutable working artifacts.
- Shipped docs (user-facing guides, API references) go in `docs/` and
  ARE committed.

## Documentation

- When `.agents/HISTORY.md` grows too large, archive older entries to
  `.agents/history/`. Reference them from `.agents/HISTORY.md`.
- Completed plans (`.agents/plans/<feature>/`) may be archived to
  `.agents/history/<feature>/` for long-term reference.
- `.agents/history/` IS version-controlled (durable decision records).

## Size budget

- `.agents/` files: ≤ **120 lines** (always-injected context).
- Subtree `AGENTS.md`: ≤ **250 lines** (loaded cumulatively with parents).

Exceeding the budget signals restating instead of pointing, or hoarding
rationale. Cut first; split the subtree only as a last resort.

## Verification

```bash
mise all        # test + lint + format (composite task)
mise test       # pytest with coverage gate (100%)
mise lint       # ruff + ty + codespell
mise format     # ruff format + isort + clean-sort
mise format-md  # rumdl over docs/, .agents/, ./ (Markdown only)
```

Pre-commit hooks (`.pre-commit-config.yaml`) run a subset of the above on every
commit. Run `pre-commit run --all-files` to check the whole tree.

## Response format

Concise and specific. No filler, intros, or restated requirements.
Answer direct questions directly.

For review/debugging/analysis: findings with references, conclusion,
approach. Mention caveats.
