"""The extras-availability verdict: tolerate recorded upstream gaps, fail everything else.

The job exists to catch a genuine root `AGENTS.md` rule 10 violation on cells the
gating `extras` job does not cover. It has to distinguish that from a dependency
whose upstream publishes no wheel for the interpreter — a thing this repository
cannot fix. These tests pin the distinction in both directions, because either
error is expensive: a tolerated blocker ships a broken extras set, and an
intolerant parser keeps the job red forever and teaches everyone to ignore it.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "extras_availability.py"

_MISSING = "can't be installed because it doesn't have a source distribution or wheel for the current platform"


def test_the_windows_free_threaded_cell_reports_onnxruntime() -> None:
    module = _module()
    output = "\n".join(
        [
            _error("onnxruntime", "1.31.1"),
            "hint: You're on Windows (`win_amd64`), but `onnxruntime` (v1.31.1) only has wheels for "
            "the following platforms: `manylinux_2_28_aarch64`, `manylinux_2_28_x86_64`",
        ]
    )
    assert module.missing_artefacts(output) == ["onnxruntime"]


def test_the_linux_free_threaded_cell_reports_the_vllm_pin() -> None:
    module = _module()
    assert module.missing_artefacts(_error("pynvvideocodec", "2.0.4")) == ["pynvvideocodec"]


def test_a_macos_cell_reports_the_cudnn_frontend() -> None:
    module = _module()
    assert module.missing_artefacts(_error("nvidia-cudnn-frontend", "1.28.0")) == ["nvidia-cudnn-frontend"]


def test_an_unsatisfiable_graph_is_not_a_missing_artefact() -> None:
    module = _module()
    output = (
        "error: No solution found when resolving dependencies\n"
        "  cause: Because conflictprobe[b] depends on httpx>=0.28 and conflictprobe[a] "
        "depends on httpx<0.20, we can conclude that conflictprobe[a] and conflictprobe[b] are incompatible.\n"
    )
    assert module.missing_artefacts(output) == []


def test_the_same_package_twice_is_reported_once() -> None:
    module = _module()
    output = "\n".join([_error("onnxruntime", "1.31.1"), _error("onnxruntime", "1.31.1")])
    assert module.missing_artefacts(output) == ["onnxruntime"]


def test_several_gaps_in_one_cell_are_sorted() -> None:
    module = _module()
    output = "\n".join([_error("vllm", "0.33.0"), _error("onnxruntime", "1.31.1")])
    assert module.missing_artefacts(output) == ["onnxruntime", "vllm"]


def test_the_version_does_not_identify_a_gap() -> None:
    """A version bump must not silently re-open a recorded gap."""
    module = _module()
    assert module.missing_artefacts(_error("onnxruntime", "9.9.9")) == ["onnxruntime"]


@pytest.mark.parametrize("package", ["nvidia-cudnn-frontend", "onnxruntime", "pynvvideocodec", "vllm"])
def test_every_recorded_gap_names_a_reason_and_a_reentry_condition(package: str) -> None:
    module = _module()
    gap = next(gap for gap in module.KNOWN_GAPS if gap.package == package)
    assert gap.reason, f"{package} tolerates a failure without saying why"
    assert gap.reentry, f"{package} tolerates a failure without a way out"
    assert gap.reentry.startswith("when "), f"{package}'s re-entry condition must name a condition, not a task"


def test_the_gap_list_is_sorted_and_unique() -> None:
    module = _module()
    packages = [gap.package for gap in module.KNOWN_GAPS]
    assert packages == sorted(packages)
    assert len(packages) == len(set(packages))


def test_a_known_gap_passes_the_cell(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    module = _module()
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(
        module,
        "_resolve",
        lambda _python: subprocess.CompletedProcess(args=[], returncode=2, stdout=_error("onnxruntime", "1.31.1"), stderr=""),
    )
    assert module.main(["--cell", "windows-latest / 3.14t", "--python", "3.14t"]) == 0
    captured = capsys.readouterr()
    assert "known upstream artefact gap" in captured.out
    assert "::notice title=extras availability::known upstream gap: onnxruntime" in captured.out


def test_an_unknown_missing_wheel_fails_the_cell(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    module = _module()
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(
        module,
        "_resolve",
        lambda _python: subprocess.CompletedProcess(args=[], returncode=2, stdout=_error("some-new-native-lib", "1.0"), stderr=""),
    )
    assert module.main(["--cell", "windows-latest / 3.14", "--python", "3.14t"]) == 1
    captured = capsys.readouterr()
    assert "no wheel for some-new-native-lib" in captured.err
    assert "not a recorded upstream gap" in captured.err


def test_an_unrecognised_resolution_error_fails_the_cell(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    module = _module()
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(
        module,
        "_resolve",
        lambda _python: subprocess.CompletedProcess(args=[], returncode=2, stdout="", stderr="error: No solution found when resolving dependencies"),
    )
    assert module.main(["--cell", "ubuntu-latest / 3.14", "--python", "3.14t"]) == 1
    captured = capsys.readouterr()
    assert "does not recognise as an upstream artefact gap" in captured.err


def test_a_green_cell_passes_without_a_gap(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    module = _module()
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(
        module,
        "_resolve",
        lambda _python: subprocess.CompletedProcess(args=[], returncode=0, stdout="Resolved 280 packages", stderr=""),
    )
    assert module.main(["--cell", "ubuntu-latest / 3.14", "--python", "3.14t"]) == 0
    assert "every extra resolved jointly" in capsys.readouterr().out


def test_the_summary_carries_the_table_when_a_gap_applies(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = _module()
    summary = tmp_path / "summary.md"
    summary.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(
        module,
        "_resolve",
        lambda _python: subprocess.CompletedProcess(
            args=[], returncode=2, stdout="\n".join([_error("onnxruntime", "1.31.1"), _error("pynvvideocodec", "2.0.4")]), stderr=""
        ),
    )
    assert module.main(["--cell", "ubuntu-latest / 3.14t", "--python", "3.14t"]) == 0
    written = summary.read_text(encoding="utf-8")
    assert "| Known gap | Why it cannot install here | Re-entry condition |" in written
    assert "`onnxruntime`" in written
    assert "`pynvvideocodec`" in written
    assert "**OK:**" in written


def test_the_summary_marks_a_failed_cell(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = _module()
    summary = tmp_path / "summary.md"
    summary.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(
        module,
        "_resolve",
        lambda _python: subprocess.CompletedProcess(args=[], returncode=2, stdout=_error("some-new-native-lib", "1.0"), stderr=""),
    )
    assert module.main(["--cell", "macos-latest / 3.14", "--python", "3.14t"]) == 1
    written = summary.read_text(encoding="utf-8")
    assert "**FAILED:**" in written
    assert "new blocker: some-new-native-lib" in written


def _error(name: str, version: str) -> str:
    """One uv uninstallable-distribution error line, as uv formats it."""
    return f"error: Distribution `{name}=={version} @ registry+https://pypi.org/simple` {_MISSING}"


def test_the_cell_label_is_required() -> None:
    module = _module()
    with pytest.raises(SystemExit):
        module.main([])


def _module() -> ModuleType:
    """Load the script by path: `scripts/` is not an importable package.

    The module is registered in ``sys.modules`` before it executes because
    ``@dataclass`` resolves its annotations through ``sys.modules[cls.__module__]``
    — a module that is only half-loaded raises ``AttributeError: 'NoneType'
    object has no attribute '__dict__'`` from ``dataclasses``.
    """
    name = "_extras_availability"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
