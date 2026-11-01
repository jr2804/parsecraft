"""No CI step may invoke a mise task that rewrites the tree it gates.

`mise all` splits every quality arm into a fixer (`lint`, `format`, `format-md`)
and a checker (`lint-check`, `format-check`) precisely because a gate that
repairs the tree validates something the caller never had (pc-flz, pc-9p6.2).
CI has to keep the same split, and it did not: `ci.yml` ran `mise lint` — ruff
`--fix` — so a lint finding was repaired on the runner and the step exited 0.
A tree with an unused import passed the quality job while the local `mise all`
rejected it (observed 2026-11-01).

The check is mechanical rather than a list of allowed task names, so it keeps
holding as tasks are added: for every `mise <task>` a workflow runs, load that
task's definition and reject it if any of its commands rewrites files.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TASK_DIR = _ROOT / ".config" / "mise"
_WORKFLOWS = _ROOT / ".github" / "workflows"

#: Commands that rewrite tracked files. A task containing any of these is a
#: fixer: correct for a developer to run, wrong for CI to run.
_REWRITERS: tuple[str, ...] = ("--fix", "rumdl fmt", "pyreorder run")
#: `ruff format` rewrites; `ruff format --check` does not. Matched separately so
#: the check arm is not swept up with the fixer.
_FORMAT_WRITE = re.compile(r"ruff format(?!\s+--check)")
_MISE_INVOCATION = re.compile(r"\bmise\s+run\s+([a-z][a-z0-9-]*)|(?<![\w-])mise\s+([a-z][a-z0-9-]*)")
#: A job key sits at two spaces of indentation under `jobs:`.
_SIBLING_JOB = re.compile(r"^ {2}[a-z][a-z0-9_-]*:\s*$")


#: Tokens that follow `mise` but are mise's own subcommands, not tasks.
_NOT_TASKS = frozenset({"run", "exec", "x", "install", "use", "which", "env", "ls", "tasks"})


def test_no_workflow_invokes_a_rewriting_mise_task() -> None:
    definitions = _task_definitions()
    offenders: list[str] = []
    for workflow in sorted(_WORKFLOWS.glob("*.yml")):
        for task in sorted(_invoked_tasks(workflow)):
            if task not in definitions:
                # Not a mise task we define (a tool name, `mise run --help`, ...).
                continue
            for command in definitions[task]:
                if any(marker in command for marker in _REWRITERS) or _FORMAT_WRITE.search(command):
                    offenders.append(f"{workflow.name} runs `mise {task}` → {command}")
    assert not offenders, (
        "CI must run the checking arm, not the fixer: a rewriting task repairs the "
        "tree on the runner and exits 0, so the gate cannot fail — and the tree CI "
        f"validated is not the tree the contributor has. Use the -check task: {offenders}"
    )


def test_the_quality_job_checks_lint_without_fixing() -> None:
    quality = _job_block(_WORKFLOWS / "ci.yml", "quality")
    assert "mise lint-check" in quality
    assert not re.search(r"mise\s+lint\s*$", quality, re.MULTILINE)


def _job_block(workflow: Path, job: str) -> str:
    """Return the text of one job, from its key to the next sibling job key."""
    lines = workflow.read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if line.strip() == f"{job}:")
    block: list[str] = [lines[start]]
    for line in lines[start + 1 :]:
        # A sibling job is a two-space-indented key at the `jobs:` level.
        if _SIBLING_JOB.match(line):
            break
        block.append(line)
    return "\n".join(block)


def _invoked_tasks(workflow: Path) -> set[str]:
    """Every task name a workflow runs as `mise <task>`."""
    names: set[str] = set()
    for line in workflow.read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0]
        if "mise" not in stripped:
            continue
        for run_form, bare_form in _MISE_INVOCATION.findall(stripped):
            name = run_form or bare_form
            if name and name not in _NOT_TASKS:
                names.add(name)
    return names


def _task_definitions() -> dict[str, list[str]]:
    """Map every mise task name to the commands it runs, across all conf.d files."""
    definitions: dict[str, list[str]] = {}
    for path in sorted(_TASK_DIR.rglob("*.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        for name, spec in data.get("tasks", {}).items():
            run = spec.get("run")
            commands = [run] if isinstance(run, str) else list(run or [])
            definitions.setdefault(name, []).extend(commands)
    return definitions
