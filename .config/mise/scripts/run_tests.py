"""Run the test suite under coverage, keeping the data when the gate fails.

Why this is a script and not a one-liner in `quality.toml`: the old one-liner
deleted the coverage data and the HTML report unconditionally, so a FAILING
gate left the operator with ``Total coverage: 99.97%`` and no way to learn
which two statements were missed — the failure was real but undiagnosable
after the fact (observed 2026-11-01, reproduced in neither of two reruns).

Contract:

* the data file is per-pid (``.coverage.<pid>``) so concurrent runs cannot
  clash, and it is passed to pytest through ``COVERAGE_FILE``;
* on success both artifacts are removed, so a green tree stays clean;
* on failure both are kept and their paths printed, so the report can be read
  ``mise clean`` removes them.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def main(root: Path = _PROJECT_ROOT) -> int:
    """Run pytest under coverage in `root`; keep the data on failure, drop it on success."""
    pid = os.getpid()
    data = f".coverage.{pid}"
    html = f".htmlcov_{pid}"
    os.environ["COVERAGE_FILE"] = data
    code = subprocess.call(  # noqa: S603 - fixed argv, no shell, no untrusted input
        [
            sys.executable,
            "-m",
            "pytest",
            "--cov=parsecraft",
            "--cov-report=term-missing",
            f"--cov-report=html:{html}",
        ],
        cwd=root,
    )
    if code:
        print(file=sys.stderr)
        print(f"coverage data kept for inspection (gate failed): {data}", file=sys.stderr)
        print(f"inspect: uv run coverage report --data-file={data} -m", file=sys.stderr)
        print(f"html:    {html}", file=sys.stderr)
    else:
        shutil.rmtree(root / html, ignore_errors=True)
        for path in glob.glob(str(root / data)) + glob.glob(str(root / f"{data}.*")):
            os.remove(path)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
