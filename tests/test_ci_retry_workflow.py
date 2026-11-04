"""The ci-retry workflow must re-run exactly the cells that never ran.

A hosted macOS runner can be unavailable for a whole run: GitHub never acquires
the cell and cancels it, so the run turns red although no macOS code executed
(observed 2026-11-04 on push cd37a0a — the cell's own annotation says "The job
was not acquired by Runner of type hosted even after multiple attempts").

The retry that fixes that has a dangerous shape, and this module pins it:

- it must NEVER re-run a cell that FAILED — the failure is the information, and
  retrying it would let a real breakage look green;
- it must never re-run a cell a human cancelled, or one that ran and timed out;
- it must never touch a pull-request run, whose head may be a fork's `main`;
- it must be bounded, or it would fight GitHub's runner capacity forever.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _ROOT / ".github" / "workflows" / "ci-retry.yml"
_TEXT = _WORKFLOW.read_text(encoding="utf-8")


def test_the_retry_triggers_only_on_a_completed_ci_run_on_main() -> None:
    assert "workflow_run:" in _TEXT
    assert "workflows: [ci]" in _TEXT
    assert "types: [completed]" in _TEXT
    assert "branches: [main]" in _TEXT


def test_the_job_is_guarded_against_a_pull_request_run() -> None:
    """The fork-pwn vector release.yml closes: only a PUSH run on main is eligible."""
    assert "github.event.workflow_run.event == 'push'" in _TEXT
    assert "github.event.workflow_run.head_branch == 'main'" in _TEXT


def test_a_run_a_human_cancelled_is_not_retried() -> None:
    """`conclusion == 'failure'`, not merely "not success": a cancelled run stays cancelled."""
    assert "github.event.workflow_run.conclusion == 'failure'" in _TEXT
    assert "github.event.workflow_run.conclusion != 'success'" not in _TEXT


def test_only_a_cancelled_cell_that_never_got_a_runner_is_re_run() -> None:
    """Capacity, not a decision and not a failure."""
    assert "job.conclusion === 'cancelled' && !job.runner_name" in _TEXT


def test_only_one_cell_is_re_run_per_firing() -> None:
    """GitHub accepts a cell re-run only while the run is terminal; one re-run ends that.

    A second re-run in the same firing is refused ("The workflow run containing
    this job is already running", measured 2026-11-04), so the loop takes the
    first cell and the next firing takes the next one.
    """
    assert "const job = capacity[0]" in _TEXT
    assert "for (const job of capacity)" not in _TEXT


def test_the_retry_budget_is_bounded() -> None:
    """Two bounds, so a re-run can never become a loop."""
    assert "MAX_RETRIES = 6" in _TEXT
    assert "run.run_attempt > MAX_RETRIES" in _TEXT
    assert "spent >= MAX_RETRIES" in _TEXT


def test_the_job_elevates_to_actions_write_and_nothing_more() -> None:
    """Re-running a cell is an Actions-API write; the token needs nothing else."""
    assert "permissions:\n      actions: write\n      contents: read" in _TEXT
    assert "permissions:\n  contents: read" in _TEXT
