"""CLAUDE.md must not exist in this project — the rail is AGENTS.md.

The project keeps ONE instruction rail, `AGENTS.md` and its `.agents/` files.
An assistant-local rail is not tracked, not gitignored-only, and not recreated
by any tool: `bd setup <recipe>` writes one, and `bd init` reinstalls every
recipe (measured 2026-11-04 — a scratch `bd init` put CLAUDE.md and
`.claude/` back into the working tree and committed them).

The rule is therefore pinned mechanically here rather than left to memory: a
file that comes back has to fail the suite, not the review.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_no_assistant_local_rail_is_tracked() -> None:
    """CLAUDE.md and .claude/ are not project files."""
    assert _tracked("CLAUDE.md") == []
    assert _tracked(".claude") == []


def _tracked(path: str) -> list[str]:
    git = shutil.which("git")
    assert git is not None, "git is required to inspect the tracked files"
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [git, "ls-files", "--", path],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line]


def test_no_assistant_local_rail_is_in_the_working_tree() -> None:
    """Not merely untracked — absent. bd setup recreates it; this fails when it does."""
    assert not (_ROOT / "CLAUDE.md").exists()
    assert not (_ROOT / ".claude").exists()


def test_they_stay_gitignored() -> None:
    """A file that a tool recreates must not be committable by accident."""
    ignored = (_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "CLAUDE.md" in ignored
    assert ".claude/" in ignored


def test_the_beads_assistant_recipe_is_not_installed() -> None:
    """`bd setup claude` writes the rail; the recipe must stay uninstalled."""
    bd = shutil.which("bd")
    if bd is None:
        # bd is a mise tool, not a project dependency — the check needs it.
        return
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [bd, "setup", "claude", "--check"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    combined = f"{result.stdout}{result.stderr}"
    assert "No hooks installed" in combined or "not installed" in combined.lower(), combined
