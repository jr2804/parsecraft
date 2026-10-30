"""Decide whether pushing the current commit would cut a release, and say why.

The rule this exists for: ``release.yml`` gates on
``!contains(github.event.workflow_run.head_commit.message, '[skip release]')``,
so the marker in the **head commit's full message** is the only thing standing
between a dev push and a tagged, published, deployed release. Remembering to
append it by hand is exactly the kind of rule that fails silently: on
2026-10-30 eight merge commits and one fix commit on ``main`` were missing the
marker, so a user push at that moment would have cut a release from a
half-finished tree. Nothing failed, nothing warned — that is the whole problem.

This script reads the marker the same way the workflow does (substring match on
the full message, not the subject), prints the verdict, and exits non-zero when a
push would release **unless** ``--allow-release`` is passed. Development pushes
therefore fail loudly; a deliberate milestone passes the flag and says so.

Usage::

    uv run scripts/release_check.py                  # dev push: must be [skip release]
    uv run scripts/release_check.py --allow-release   # milestone: releasing is intended
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass

MARKER = "[skip release]"
_SUBJECT_LIMIT = 72


@dataclass(frozen=True, slots=True)
class Verdict:
    """What a push of the current HEAD would do."""

    subject: str
    carries_marker: bool
    ahead_of_origin: int
    already_tagged: str | None
    branch: str

    @property
    def on_main(self) -> bool:
        """Only a push to ``main`` can release; release.yml checks head_branch."""
        return self.branch == "main"

    @property
    def will_release(self) -> bool:
        """True when a push would tag, publish and deploy.

        Scoped to ``main`` on purpose: release.yml's ``if:`` requires
        ``head_branch == 'main'``, so a feature-branch push cannot release no
        matter what its message says. Checking only the marker would fail CI for
        a contributor whose HEAD commit happens to lack it, which is noise.
        """
        return self.on_main and not self.carries_marker and self.already_tagged is None


def main(argv: list[str]) -> int:
    """Report the verdict; gate on intent rather than on memory."""
    allow_release = "--allow-release" in argv
    unknown = [arg for arg in argv if arg not in {"--allow-release"}]
    if unknown:
        print(f"unknown argument(s): {' '.join(unknown)}", file=sys.stderr)
        return 2

    verdict = inspect()
    subject = verdict.subject
    if len(subject) > _SUBJECT_LIMIT:
        subject = subject[: _SUBJECT_LIMIT - 3] + "..."

    print(f"HEAD: {subject}")
    print(f"branch: {verdict.branch}")
    print(f"marker {MARKER!r} in the full message: {verdict.carries_marker}")
    if verdict.already_tagged is not None:
        print(f"already tagged {verdict.already_tagged}: a push reuses the tag (idempotent)")
    print(f"commits ahead of origin/main: {verdict.ahead_of_origin}")

    if not verdict.on_main:
        print(f"\nVERDICT: on {verdict.branch!r}, not 'main' - release.yml requires main, so this push cannot release.")
        return 0

    if verdict.already_tagged is not None:
        print("\nVERDICT: tagged commit - a push reuses the tag, nothing new is published.")
        return 0

    if not verdict.will_release:
        print("\nVERDICT: development push - CI validates it, nothing is released.")
        return 0

    if allow_release:
        print("\nVERDICT: RELEASE push - this tags, publishes to PyPI and deploys the docs.")
        print("Confirm that is intended; `mise all` must be green on the tree you push.")
        return 0

    print("\nVERDICT: this push WOULD cut a release, and the head commit has no marker.")
    print("A development commit must carry the marker in its full message:")
    print(f"  git commit --amend -m '<subject> {MARKER}'")
    print(f"If a release IS intended, re-run with --allow-release: {sys.argv[0]} --allow-release")
    return 1


def inspect() -> Verdict:
    """Read the tree's release posture without touching anything."""
    return Verdict(
        subject=head_subject(),
        carries_marker=MARKER in head_message(),
        ahead_of_origin=commits_ahead_of_origin(),
        already_tagged=tag_pointing_at_head(),
        branch=current_branch(),
    )


def head_message() -> str:
    """Full message of HEAD — the same field the workflow inspects."""
    return _git("log", "-1", "--format=%B").strip()


def head_subject() -> str:
    """First line of HEAD's message, for reporting only."""
    return _git("log", "-1", "--format=%s").strip()


def commits_ahead_of_origin() -> int:
    """How many commits a push of this branch would send (0 means already pushed)."""
    count = _git("rev-list", "--count", "origin/main..HEAD").strip()
    return int(count)


def tag_pointing_at_head() -> str | None:
    """A CalVer tag already on HEAD, if any - a tagged push is idempotent, not a release."""
    tags = _git("tag", "--points-at", "HEAD").split()
    calver = [tag for tag in tags if tag[:1].isdigit()]
    return max(calver, default=None)


def current_branch() -> str:
    """Short name of the checked-out branch (``HEAD`` when detached)."""
    return _git("rev-parse", "--abbrev-ref", "HEAD").strip()


def _git(*args: str) -> str:
    """Run one git command and return its stdout, failing loudly.

    ``shutil.which`` resolves the executable to a full path rather than relying
    on PATH lookup at spawn time (ruff's S607); a missing git is a clear message
    instead of an OSError from deep inside ``subprocess``.
    """
    git = shutil.which("git")
    if git is None:
        raise SystemExit("git is not on PATH - cannot inspect the release posture")
    result = subprocess.run(  # noqa: S603 - resolved absolute path, fixed argv, no shell
        [git, *args], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
