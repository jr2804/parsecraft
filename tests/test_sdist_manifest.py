"""Keep pyproject's sdist exclude list in step with nested .gitignore files.

Hatchling honours the root `.gitignore` but not the per-directory ones, so a
directory hidden only by a *nested* `.gitignore` is shipped by a local
`uv build --sdist` while a fresh CI checkout never has it (pc-1qz). The wheel and
the published artifact are unaffected; the local build is the leak.

This test walks the tree once, finds every nested `.gitignore`, asks git which
rule actually ignores a probe inside it, and requires every directory ignored by
a nested rule (rather than by the root `.gitignore`) to appear in
`[tool.hatch.build.targets.sdist].exclude`. It fails when a new nested ignore
appears without an exclude entry, which is the only way this list can rot.
"""

from __future__ import annotations

import shutil
import subprocess  # noqa: S404 - resolved binary, fixed argv, no shell
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PROBE = "__sdist_probe__"
_ROOT_IGNORE = ".gitignore"


def test_every_directory_hidden_by_a_nested_gitignore_is_excluded_from_the_sdist() -> None:
    exclude = _exclude_list()
    missing: list[str] = []
    for relative in _nested_gitignore_dirs():
        source = _ignore_source(relative)
        if source is None or source == _ROOT_IGNORE:
            # Hatchling reads the root ignore, and an untracked directory no rule
            # hides is a different problem (it would ship from a clean checkout
            # too, so the sdist exclude list is not the right lever).
            continue
        if not _covered(relative, exclude):
            missing.append(f"{relative} (hidden by {source})")
    assert not missing, (
        "these directories are hidden only by a nested .gitignore, so Hatchling would "
        f"ship them in a local sdist: {missing}; add them to "
        "[tool.hatch.build.targets.sdist].exclude in pyproject.toml"
    )


def test_the_sdist_exclude_list_covers_the_measured_leaks() -> None:
    exclude = _exclude_list()
    for entry in (".beads", ".cache", ".rumdl_cache", ".tmp", "tests/test-cache"):
        assert entry in exclude, entry


def _nested_gitignore_dirs() -> list[str]:
    directories: list[str] = []
    for ignore in sorted(_ROOT.rglob(".gitignore")):
        relative = ignore.parent.relative_to(_ROOT)
        if relative == Path("."):
            continue
        if not ignore.parent.is_dir():
            continue
        directories.append(relative.as_posix())
    return directories


def _ignore_source(relative: str) -> str | None:
    """Return the ignore file that hides a probe inside ``relative``, if any."""
    git = shutil.which("git")
    assert git is not None, "git is required to determine ignore rules"
    result = subprocess.run(  # noqa: S603 - resolved binary, fixed argv, no shell
        [git, "check-ignore", "-v", "--no-index", f"{relative}/{_PROBE}"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.split(":", 1)[0].strip()


def _covered(relative: str, exclude: list[str]) -> bool:
    return any(relative == entry or relative.startswith(f"{entry}/") for entry in exclude)


def _exclude_list() -> list[str]:
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sdist = pyproject["tool"]["hatch"]["build"]["targets"]["sdist"]
    return list(sdist["exclude"])
