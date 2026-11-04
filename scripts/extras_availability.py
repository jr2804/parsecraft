"""Decide whether an unavailable extra is a known upstream gap or a new blocker.

Why this exists
---------------

The `extras-availability` CI job resolves `--all-extras` on the cells the
gating `extras` job does not cover (CPython 3.14 and 3.14t on all three OSes).
Some of those cells cannot pass, and not because of anything this repository
controls: a dependency whose upstream publishes no wheel for that interpreter
and platform *and* no sdist simply cannot be installed. PEP 508 has no
free-threaded marker, so such a package cannot be marked out of a bulk
`--all-extras` install either.

The job used to run `uv sync --dry-run` with `continue-on-error: true`. That is
the worst of both worlds: the run stays green for every cause, so a genuinely
incompatible extras set — an actual root `AGENTS.md` rule 10 violation, the one
thing this job exists to catch — looks exactly like the upstream gaps, and the
job shows a red X that has to be read and dismissed on every single run
(`pc-7qq`). Alert fatigue is not a cosmetic problem: a permanently red cell is
a cell nobody reads.

So the job now makes the distinction explicitly and mechanically:

* a **known gap** (listed in `KNOWN_GAPS` with its reason and re-entry
  condition) is reported as a notice and the cell passes;
* anything else — a missing wheel this file has never seen, an unsatisfiable
  dependency graph, a resolver error of any other shape — fails the cell.

Anything the classifier does not positively recognise is a failure. A parser
over another tool's output is a trust boundary, and the safe default there is
to escalate, never to assume.

Re-entry
--------

When an upstream publishes the missing wheel, the cell goes green and the
corresponding `KNOWN_GAPS` entry stops appearing in any step summary. That is
the signal to delete the entry: the list is meant to shrink to nothing, at
which point this script and the job can be replaced by a plain `uv sync`.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: The rule 10 assertion. Identical to the gating `extras` job's command, so a
#: cell that passes here would pass there. `--python` is appended per cell: the
#: assertion is about the CELL's interpreter, and without it uv silently uses
#: whatever venv sits in the checkout — which is how a 3.14t cell reported green
#: while the 3.14t resolution was failing (observed 2026-11-04).
_RESOLVE_ARGV: tuple[str, ...] = (
    "uv",
    "sync",
    "-U",
    "--dry-run",
    "--all-extras",
    "--all-groups",
    "--all-packages",
)

#: uv's wording when a requirement has no artefact for this interpreter and
#: platform. Matched on the fixed phrase, not the whole line, because the
#: package spec in the middle carries a registry URL.
_MISSING_ARTEFACT = re.compile(
    r"Distribution `(?P<spec>[^`]+)` can't be installed because it doesn't have "
    r"a source distribution or wheel for the current platform",
)
#: A uv spec is ``name==version @ source``; the name is all that identifies the
#: upstream, and a version bump must not silently re-open a known gap.
_SPEC_NAME = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)")


@dataclass(frozen=True)
class KnownGap:
    """An upstream artefact gap this repository cannot fix and must tolerate."""

    package: str
    reason: str
    reentry: str


#: Every entry carries its own re-entry condition (``pc-7qq``): a tolerance
#: without one becomes permanent. Keep this list sorted by package.
KNOWN_GAPS: tuple[KnownGap, ...] = (
    KnownGap(
        package="nvidia-cudnn-frontend",
        reason=("reached only through `vllm`, which publishes no macOS wheel; nvidia-cudnn-frontend ships Linux and Windows wheels only and no sdist"),
        reentry="when vllm or nvidia-cudnn-frontend publishes a macOS wheel",
    ),
    KnownGap(
        package="onnxruntime",
        reason=("reached through `pymupdf`; onnxruntime publishes `cp314t` wheels for manylinux only, no free-threaded macOS/Windows wheel and no sdist"),
        reentry="when onnxruntime publishes free-threaded macOS/Windows wheels",
    ),
    KnownGap(
        package="pynvvideocodec",
        reason=("pinned exactly to 2.0.4 by `vllm`; that release ships `cp314` wheels only, with no `cp314t` wheel on any platform and no sdist"),
        reentry="when vllm's pin moves to a release with free-threaded wheels",
    ),
    KnownGap(
        package="vllm",
        reason=("publishes manylinux wheels and an sdist, so a macOS or free-threaded cell would have to build it from source"),
        reentry="when vllm publishes macOS or free-threaded wheels",
    ),
)

_BY_PACKAGE: dict[str, KnownGap] = {gap.package: gap for gap in KNOWN_GAPS}


def main(argv: list[str] | None = None) -> int:
    """Run the assertion for one cell and report the verdict."""
    args = _parse_args(argv)
    result = _resolve(args.python)
    output = result.stdout + result.stderr
    if result.returncode == 0:
        _write_summary(args.cell, [], verdict="every extra resolved jointly")
        return 0
    blockers = missing_artefacts(output)
    if not blockers:
        # Not a wheel gap: an unsatisfiable graph, a build failure, a broken
        # resolver. Never tolerate it — this is exactly what rule 10 forbids.
        print(
            f"extras availability ({args.cell}): the resolution failed for a reason "
            "this script does not recognise as an upstream artefact gap. "
            "Treating it as a real blocker.\n",
            file=sys.stderr,
        )
        print(output.strip(), file=sys.stderr)
        _write_summary(args.cell, [], verdict="FAILED — unrecognised resolution error", failed=True)
        return 1
    unknown = [name for name in blockers if name not in _BY_PACKAGE]
    if unknown:
        print(
            f"extras availability ({args.cell}): no wheel for {', '.join(unknown)}. "
            "This package is not a recorded upstream gap, so the failure is real. "
            "Either drop the dependency or add a KNOWN_GAPS entry with a reason "
            "and a re-entry condition (root AGENTS.md rule 10).",
            file=sys.stderr,
        )
        _write_summary(
            args.cell,
            [gap for name in blockers if (gap := _BY_PACKAGE.get(name)) is not None],
            verdict=f"FAILED — new blocker: {', '.join(unknown)}",
            failed=True,
        )
        return 1
    applied = [_BY_PACKAGE[name] for name in blockers]
    for gap in applied:
        print(
            f"::notice title=extras availability::known upstream gap: {gap.package} (see the step summary; re-entry: {gap.reentry})",
        )
    print(
        f"extras availability ({args.cell}): {len(applied)} known upstream artefact gap(s), no new blocker.",
    )
    _write_summary(args.cell, applied, verdict="known upstream gaps only, no new blocker")
    return 0


def missing_artefacts(output: str) -> list[str]:
    """Distinct package names uv reports as uninstallable on this platform.

    Sorted so a cell's summary is stable across runs, and unique because uv may
    repeat the same package in its hint block.
    """
    names: set[str] = set()
    for match in _MISSING_ARTEFACT.finditer(output):
        name = _package_name(match.group("spec"))
        if name is not None:
            names.add(name)
    return sorted(names)


def _package_name(spec: str) -> str | None:
    """The distribution name from a uv spec (`name==version @ source`)."""
    match = _SPEC_NAME.match(spec.strip())
    return match.group("name") if match else None


def _resolve(python: str) -> subprocess.CompletedProcess[str]:
    """Run the rule 10 assertion for ``python``, capturing both streams.

    ``UV_PROJECT_ENVIRONMENT`` is redirected into a throwaway directory: a
    ``.venv`` in the checkout would otherwise answer for the cell's interpreter
    and hide the very resolution this job exists to check.
    """
    environment = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(_throwaway_environment())}
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, no untrusted input
        [*_RESOLVE_ARGV, "--python", python],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def _throwaway_environment() -> Path:
    """A per-run project environment path that no checkout venv can shadow."""
    directory = Path(tempfile.gettempdir()) / "parsecraft-extras-availability"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "venv"


def _write_summary(cell: str, applied: Sequence[KnownGap], *, verdict: str, failed: bool = False) -> None:
    """Append this cell's verdict to the GitHub step summary, when there is one."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    mark = "FAILED" if failed else "OK"
    lines = [f"### extras availability - {cell}", "", f"**{mark}:** {verdict}"]
    if applied:
        lines.extend(["", "| Known gap | Why it cannot install here | Re-entry condition |", "| --- | --- | --- |"])
        lines.extend(f"| `{gap.package}` | {gap.reason} | {gap.reentry} |" for gap in applied)
    text = "\n".join(lines) + "\n"
    print(text)
    if path:
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the cell label used in messages and the interpreter it asserts about."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cell", required=True, help='matrix cell label, e.g. "ubuntu-latest / 3.14t"')
    parser.add_argument("--python", required=True, help='the cell interpreter, e.g. "3.14t"')
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
