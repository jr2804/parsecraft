"""Packaging contract: py.typed ships, wheel metadata stays core-only."""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest

_PKG_DIR = Path("src/parsecraft")
_PYPROJECT = Path("pyproject.toml")


def test_py_typed_marker_exists() -> None:
    assert (_PKG_DIR / "py.typed").is_file()


def test_wheel_contains_py_typed_and_ir(wheel_path: Path) -> None:
    with zipfile.ZipFile(wheel_path) as archive:
        names = set(archive.namelist())
    assert "parsecraft/py.typed" in names
    assert "parsecraft/ir/models.py" in names
    assert "parsecraft/backends/registry.py" in names


def test_wheel_metadata_declares_core_dependencies_only(wheel_path: Path) -> None:
    """The wheel's runtime requirements must equal pyproject's [project] core set."""
    declared = _declared_core_dependencies()
    with zipfile.ZipFile(wheel_path) as archive:
        metadata_name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8")
    core: set[str] = set()
    for line in metadata.splitlines():
        if not line.startswith("Requires-Dist:"):
            continue
        requirement = line.removeprefix("Requires-Dist:").strip()
        if "extra ==" in requirement:  # optional extras are not core runtime deps
            continue
        name_match = re.match(r"[A-Za-z0-9_.-]+", requirement)
        assert name_match is not None, requirement
        core.add(name_match.group(0))
    assert core == declared
    assert "Requires-Python: >=3.13" in metadata


def _declared_core_dependencies() -> set[str]:
    """Read core dependency names from pyproject (single source of truth)."""
    raw = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    declared: set[str] = set()
    for requirement in raw["project"]["dependencies"]:
        name_match = re.match(r"[A-Za-z0-9_.-]+", requirement.strip())
        assert name_match is not None, requirement
        declared.add(name_match.group(0))
    return declared


@pytest.fixture(scope="session")
def wheel_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the wheel once per session and return its path."""
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv not available for wheel build")
    out_dir = tmp_path_factory.mktemp("wheel")
    proc = subprocess.run(  # noqa: S603
        [uv, "build", "--wheel", "--out-dir", str(out_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    wheels = list(out_dir.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]
