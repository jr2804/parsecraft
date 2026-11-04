"""The macOS leg must never hold up a release.

A hosted macOS runner can be unavailable for an entire run: GitHub never hands
the cell to a runner and cancels it, so the run turns red although no macOS
code ever ran (observed 2026-11-04 on push cd37a0a). A macOS cell that TIMES
OUT must not block the release (user ruling 2026-11-04).

It could not be fixed inside `ci.yml`: a job still waiting for a runner keeps
the RUN open, so the release — which waits for the ci run to complete — would
wait for the macOS leg either way, and a cancelled cell can still decide the
run's conclusion. So the macOS legs live in their own workflow, `ci-macos.yml`,
which the release never waits for. The macOS leg is still tested, still
reported on the commit, and still retried by `ci-retry.yml` — it simply cannot
gate a release.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CI = _ROOT / ".github" / "workflows" / "ci.yml"
_CI_MACOS = _ROOT / ".github" / "workflows" / "ci-macos.yml"
_CI_RETRY = _ROOT / ".github" / "workflows" / "ci-retry.yml"


def test_the_gated_workflow_has_no_macos_cell() -> None:
    """Nothing macOS may run inside the workflow the release waits for."""
    ci = _CI.read_text(encoding="utf-8")
    assert "macos-latest" not in ci
    # The matrix itself - the comments in it may name macOS (the suspended
    # free-threaded cells), so they are stripped before the check.
    matrix = re.search(r"^      matrix:\n(.*?)(?=^    runs-on:)", _job_block(ci, "tests"), re.S | re.M)
    assert matrix is not None
    body = "\n".join(line for line in matrix.group(1).splitlines() if not line.strip().startswith("#"))
    assert "macos" not in body.lower()


def test_the_macos_leg_has_its_own_workflow() -> None:
    """Its own workflow is what makes it non-blocking for the release."""
    macos = _CI_MACOS.read_text(encoding="utf-8")
    assert "runs-on: macos-latest" in macos
    assert "tests-macos" in macos
    assert "extras-availability-macos" in macos


def test_the_macos_leg_runs_exactly_the_checks_the_gated_one_runs() -> None:
    """The macOS leg must not quietly test less than the others."""
    ci = _CI.read_text(encoding="utf-8")
    macos = _CI_MACOS.read_text(encoding="utf-8")
    assert _steps(macos, "tests-macos") == _steps(ci, "tests")


def _steps(text: str, job: str) -> list[str]:
    """The `uses:`/`run:` lines of a job - what it actually executes."""
    return [line.strip() for line in _job_block(text, job).splitlines() if "uses:" in line or "run:" in line]


def test_the_macos_extras_cells_use_the_same_verdict_script() -> None:
    """The macOS cells classify their failures exactly as ci.yml's do (pc-7qq).

    Tolerance is declared per recorded gap, never assumed by `continue-on-error`
    — so the macOS cells must run the same script, not a bare `uv sync` under a
    blanket tolerance.
    """
    macos = _CI_MACOS.read_text(encoding="utf-8")
    job = _job_block(macos, "extras-availability-macos")
    assert "scripts/extras_availability.py" in job
    assert "uv run --no-project python" in job
    assert "continue-on-error" not in job


def _job_block(text: str, name: str) -> str:
    """The YAML block of one job, up to the next job or the end of the file."""
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [a-z][a-z0-9_-]*:\n|\Z)", text, re.S | re.M)
    assert match is not None, f"job {name} is missing"
    return match.group(1)


def test_the_release_waits_only_for_the_gated_workflow() -> None:
    """The release is triggered by ci, never by the macOS leg."""
    release = (_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "workflows: [ci]" in release
    assert "ci-macos" not in release


def test_the_retry_workflow_covers_both_workflows() -> None:
    """The macOS leg's capacity-cancelled cells are retried too."""
    retry = _CI_RETRY.read_text(encoding="utf-8")
    assert "workflows: [ci, ci-macos]" in retry
