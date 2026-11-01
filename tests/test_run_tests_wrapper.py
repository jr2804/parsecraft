"""Contract tests for the `mise test` wrapper (`.config/mise/scripts/run_tests.py`).

The wrapper exists so a FAILING coverage gate leaves its per-line data behind.
Until 2026-11-01 it deleted that data unconditionally, which is why a real
``Total coverage: 99.97%`` failure could not be diagnosed after the fact. The
two branches are asymmetric on purpose and both are pinned here: a green run
must not litter the tree, a red run must keep enough to read the report.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(".config/mise/scripts/run_tests.py")


def test_a_failing_gate_keeps_the_coverage_data_and_the_html_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The whole point of the wrapper: red must stay diagnosable afterwards."""
    module = _load_wrapper()
    _stub_pytest(monkeypatch, module, code=1)
    data, html = _artifacts(tmp_path)
    data.write_text("data", encoding="utf-8")
    html.mkdir()

    assert module.main(root=tmp_path) == 1

    assert data.exists()
    assert html.is_dir()
    message = capsys.readouterr().err
    assert f"coverage data kept for inspection (gate failed): {data.name}" in message
    assert f"--data-file={data.name}" in message
    assert html.name in message


def test_a_green_gate_removes_its_own_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A passing run must not leave per-pid files behind for `mise clean` to chase."""
    module = _load_wrapper()
    _stub_pytest(monkeypatch, module, code=0)
    data, html = _artifacts(tmp_path)
    data.write_text("data", encoding="utf-8")
    journal = tmp_path / f"{data.name}.journal"
    journal.write_text("j", encoding="utf-8")
    html.mkdir()

    assert module.main(root=tmp_path) == 0

    assert not data.exists()
    assert not journal.exists()
    assert not html.exists()


def _artifacts(root: Path) -> tuple[Path, Path]:
    pid = os.getpid()
    return root / f".coverage.{pid}", root / f".htmlcov_{pid}"


def test_the_wrapper_invokes_pytest_with_coverage_for_this_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The child is pytest itself, so the gate is the same one CI runs."""
    module = _load_wrapper()
    seen = _stub_pytest(monkeypatch, module, code=0)

    module.main(root=tmp_path)

    assert len(seen) == 1
    argv = seen[0]
    assert argv[:3] == [sys.executable, "-m", "pytest"]
    assert "--cov=parsecraft" in argv
    assert "--cov-report=term-missing" in argv
    assert any(part.startswith("--cov-report=html:.htmlcov_") for part in argv)


def _stub_pytest(monkeypatch: pytest.MonkeyPatch, module: ModuleType, code: int) -> list[list[str]]:
    """Replace the pytest call; returns the list of argv lists it was given."""
    seen: list[list[str]] = []

    def _fake_call(argv: list[str], **kwargs: object) -> int:
        seen.append(argv)
        assert kwargs["cwd"] is not None
        return code

    monkeypatch.setattr(module.subprocess, "call", _fake_call)
    return seen


def test_the_wrapper_runs_in_the_given_root_and_sets_a_per_pid_data_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The data path is relative, so the child must run where the tree is."""
    module = _load_wrapper()
    roots: list[object] = []

    def _fake_call(_argv: list[str], **kwargs: object) -> int:
        roots.append(kwargs["cwd"])
        return 0

    monkeypatch.setattr(module.subprocess, "call", _fake_call)

    module.main(root=tmp_path)

    assert roots == [tmp_path]
    assert os.environ["COVERAGE_FILE"] == f".coverage.{os.getpid()}"


def _load_wrapper() -> ModuleType:
    """Import the wrapper by path; it is a script, not an installed module."""
    spec = importlib.util.spec_from_file_location("_run_tests_wrapper", _SCRIPT.resolve())
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
