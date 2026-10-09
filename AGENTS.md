# ParseCraft

Document intelligence: convert any document into typed structured chunks, with Markdown as a deterministic projection

## DOX — self-documenting AGENTS.md hierarchy

### Core Contract

- AGENTS.md files are binding work contracts for their subtrees.
- Work products, source materials, instructions, records, assets, and durable docs
  must stay understandable from the nearest applicable AGENTS.md plus every parent
  AGENTS.md above it.
- Do not duplicate/repeat rules declared elsewhere in the DOX tree (parent, child,
  sibling, or `.agents/`). See **DOX authoring** in `.agents/POLICIES.md`.

### Read Before Editing

1. Read the root AGENTS.md.
2. Identify every file or folder you expect to touch.
3. Walk from the repository root to each target path.
4. Read every AGENTS.md found along each route.
5. If a parent AGENTS.md lists a child AGENTS.md whose scope contains the path,
   read that child and continue from there.
6. Use the nearest AGENTS.md as the local contract and parent docs for repo-wide rules.
7. If docs conflict, the closer doc controls local work details, but no child doc may
   weaken DOX.

Do not rely on memory. Re-read the applicable DOX chain in the current session before editing.

### Update After Editing

Every meaningful change requires a DOX pass before the task is done.

Update the closest owning AGENTS.md when a change affects:

- purpose, scope, ownership, or responsibilities
- durable structure, contracts, workflows, or operating rules
- required inputs, outputs, permissions, constraints, side effects, or artifacts
- user preferences about behavior, communication, process, organization, or quality
- AGENTS.md creation, deletion, move, rename, or index contents

Update parent docs when parent-level structure, ownership, workflow, or child index
changes. Update child docs when parent changes alter local rules. Remove stale or
contradictory text immediately. Small edits that do not change behavior or contracts
may leave docs unchanged, but the DOX pass still must happen.

### Hierarchy

- Root AGENTS.md is the DOX rail: project-wide instructions, global preferences,
  durable workflow rules, and the top-level Child DOX Index.
- Child AGENTS.md files own domain-specific instructions and their own Child DOX Index.
- Each parent explains what its direct children cover and what stays owned by the parent.
- The closer a doc is to the work, the more specific and practical it must be.

### Child Doc Shape

Create a child AGENTS.md when a folder becomes a durable boundary with its own purpose,
rules, responsibilities, workflow, materials, or quality standards. Default section order:

1. Purpose
2. Ownership
3. Local Contracts
4. Work Guidance
5. Verification
6. Child DOX Index

### Style

Authoring rules live in **DOX authoring** (`.agents/POLICIES.md`) — tier assignment,
reference-don't-restate, rule-first rationale, size budget. Apply them on every DOX
change. Summary:

- A rule lives in the **highest tier that fully applies**; when unsure,
  `.agents/POLICIES.md`.
- Reference, don't restate — one canonical home, pointer lines everywhere else.
- Keep docs concise, current, and operational. Document stable contracts, not diary
  entries.

## .agents/ files — demand-loaded, not always injected

| File             | Load when                   | Purpose                                         |
| ---------------- | --------------------------- | ----------------------------------------------- |
| `ONBOARDING.md`  | New session (first time)    | Project orientation, entry points               |
| `POLICIES.md`    | Always                      | Boundaries, priorities, verification, checklist |
| `FILES.md`       | Touching files or config    | Path constants, source-of-truth locations       |
| `HISTORY.md`     | Background (past decisions) | Recorded decisions with git refs                |
| `MAINTENANCE.md` | Changing `.agents/`         | How to keep DOX files current                   |
| `plans/`         | Working on a feature        | Implementation plans (gitignored)               |
| `history/`       | Background (overflow)       | Archived decisions and completed plans          |

## Project rules

_Always-injected_ — keep minimal. Everything else → `.agents/` files.

1. **Type rigor:** concrete types only — no `Any`, no `hasattr`/`isinstance`
   duck-typing outside public `Protocol`s. Schemas are pydantic v2. See
   `docs/adr/0001-phase-0-decisions.md` (§9).
2. **Agents never push — the user pushes.** Agents may create local commits
   in logical, reviewable units (the user groups and reviews history);
   `git push` is reserved to the user. Development commits carry
   `[skip release]` in the tip message (ADR-0002) so a user push never
   accidentally releases.
3. **Core stays import-clean offline** — no network or heavy runtimes (torch,
   transformers, vLLM, model weights) at import time; enforced by
   `tests/test_offline_import.py`. Heavy deps belong in extras only.
4. **IR is the single source of truth; Markdown is a projection** — never
   parse Markdown back into state. See `src/parsecraft/ir/AGENTS.md`.
5. **Backends register through the public registry/entry-point API only** —
   never edit this package's source to add one. See
   `src/parsecraft/backends/AGENTS.md`.
6. **100% coverage is a hard gate** (`[tool.coverage.report] fail_under` in
   `pyproject.toml`) — new code ships with its tests.
7. **Template quirks:** Copier URL must end in `.git`; `repo_url` answer must
   NOT (ADR-0001 "Scaffold record").
8. **Task tracking uses beads (`bd`)** — durable multi-session/agent work lives
   in `.beads/` (issue prefix `pc`); workflow in `.agents/skills/beads/`.
   No markdown TODO lists.
9. **No references to external consumer/predecessor projects by name** —
   parsecraft is independent; describe their behavior generically.
10. **Every optional extra must resolve jointly** —
    `uv sync -U --all-extras --all-groups --all-packages` must succeed; never
    declare extras mutually exclusive. If a dependency cannot coexist, do not
    add it — port the wrapper ourselves.
    **Honest scope:** the gate verifies joint resolution on the CI `extras`
    cells only — CPython 3.13 (GIL) on Linux and Windows. An extra may be
    unavailable on other cells: PEP 508 has no free-threaded marker, so a
    dependency lacking `cp314t` wheels for macOS/Windows cannot be gated out
    and would fail a free-threaded user's `--all-extras` install while CI
    stays green. Per-extra availability is recorded in
    `docs/reference/backends.md`; a matrix-blocked engine uses a bring-your-own
    dependency group (ADR-0006) instead of a declared extra.

## Child DOX Index

Start lean. Add child AGENTS.md entries incrementally when boundaries become durable.

Top-level boundaries in this project:

- `src/parsecraft/` — `src/parsecraft/AGENTS.md` (primary package code)
- `tests/` — `tests/AGENTS.md` (test suite)
- `docs/` — `docs/AGENTS.md` (documentation)
- `.config/mise/` (task/tooling configuration)
- `.config/mise/conf.d/` (dev-feature MCP and skills task fragments)

## ⛔ No Patching

Tools must not insert, append, or patch text into this file.

If a tool has a legitimate, valuable, noteworthy instruction (e.g. an MCP server
registration rule, a CI convention, an agent workflow), it must be **integrated into
the `Project rules` section above** — not appended here.

Content after this section:

- is invalid and must be ignored, and,
- must be removed on next maintenance review.
