"""Real-engine smoke test: run every opt-in CLI path once, against a real document.

The rule this exists for: stub tests prove the wiring, not the seam. The
liteparse vector-graphics crash of 2026.10.7 shipped because the adapter's
handoff from liteparse's real ``VectorGraphics`` dataclass was never executed —
the suite built the parsed structure *after* that handoff, so 100 % coverage sat
exactly on top of the untested seam. This script converts a real PDF through the
real engines for every opt-in path, so an interface mismatch fails here instead
of in a consumer's conversion.

Runs offline, needs only base-install engines (native family + liteparse), and
takes seconds. Invoked by ``mise run smoke``; the release workflow is expected to
run it (or the human to) before a deliberate release.

Usage: ``uv run scripts/smoke.py [--verbose]``
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TIMEOUT_S = 180


def _build_pdf() -> bytes:
    """A real one-page PDF: a heading, a horizontal rule, a body line.

    Written by hand so the smoke owns its input and needs no committed binary
    (ADR-0001 §8). The rule is a filled rectangle, which is what liteparse
    reports as a vector line — the input the table path consumes.
    """
    content = b"BT /F1 12 Tf 72 700 Td (Smoke heading) Tj ET\n0 0 0 rg\n72 640 400 2 re f\nBT /F1 12 Tf 72 600 Td (Body text for the smoke test.) Tj ET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


@dataclass(frozen=True, slots=True)
class _Case:
    """One opt-in CLI path: what the operator would type, and why it matters."""

    label: str
    args: tuple[str, ...]
    why: str


def _cases(pdf: Path) -> list[_Case]:
    return [
        _Case("default (native family)", (str(pdf),), "the base path every consumer uses"),
        _Case(
            "liteparse",
            (str(pdf), "--backend", "liteparse"),
            "explicit backend selection",
        ),
        _Case(
            "liteparse + text metadata",
            (str(pdf), "--backend", "liteparse", "--backend-option", "extract_text_metadata=true"),
            "option plumbing reaches the engine (a value it validates)",
        ),
        _Case(
            "liteparse + vector graphics",
            (str(pdf), "--backend", "liteparse", "--backend-option", "extract_vector_graphics=true"),
            "issue #7: the VectorGraphics handoff into the table path",
        ),
        _Case(
            "liteparse + unknown option value",
            (str(pdf), "--backend", "liteparse", "--backend-option", "extract_blocks=banana"),
            "an invalid option value is rejected loudly, never silently ignored",
        ),
    ]


def _run(args: tuple[str, ...], output_file: Path) -> tuple[int, str]:
    command = [
        sys.executable,
        "-m",
        "parsecraft",
        "convert",
        *args,
        "--no-cache",
        "--json",
        "-o",
        str(output_file),
    ]
    completed = subprocess.run(  # noqa: S603 - fixed argv from _cases, no shell
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_S,
        check=False,
    )
    return completed.returncode, f"{completed.stdout}{completed.stderr}"


def _report(output_file: Path) -> str:
    """A compact census of what the run produced, or the error text."""
    if not output_file.exists():
        return "no output written"
    payload = json.loads(output_file.read_text(encoding="utf-8"))
    pages = payload.get("pages") or []
    kinds: dict[str, int] = {}
    for page in pages:
        for block in page.get("blocks") or []:
            kind = str(block.get("kind"))
            kinds[kind] = kinds.get(kind, 0) + 1
    census = ", ".join(f"{name}={count}" for name, count in sorted(kinds.items())) or "no blocks"
    return f"{len(pages)} page(s): {census}"


def main(argv: list[str]) -> int:
    verbose = "--verbose" in argv
    with tempfile.TemporaryDirectory(prefix="parsecraft-smoke-") as work_dir:
        work = Path(work_dir)
        pdf = work / "smoke.pdf"
        pdf.write_bytes(_build_pdf())
        print(f"smoke: real PDF {pdf.stat().st_size} bytes, {len(_cases(pdf))} paths\n")

        failures: list[str] = []
        for case in _cases(pdf):
            output_file = work / f"{abs(hash(case.label))}.json"
            code, text = _run(case.args, output_file)
            # The rejection case MUST fail; every other case MUST succeed.
            expected_failure = "unknown option value" in case.label
            ok = code != 0 if expected_failure else code == 0
            detail = "rejected loudly" if expected_failure and code != 0 else _report(output_file)
            if expected_failure and code == 0:
                detail = "ACCEPTED a bogus option value"
            status = "ok  " if ok else "FAIL"
            print(f"  [{status}] {case.label:<32} exit={code}  {detail}")
            if verbose or not ok:
                print(f"         why: {case.why}")
                if not ok and text.strip():
                    print("         " + text.strip().splitlines()[-1][:200])
            if not ok:
                failures.append(case.label)

    if failures:
        print(f"\nsmoke FAILED: {len(failures)} path(s): {', '.join(failures)}")
        return 1
    print("\nsmoke passed: every opt-in path ran against the real engines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
