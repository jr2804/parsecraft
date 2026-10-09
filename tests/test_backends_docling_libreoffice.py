"""LibreOffice discovery for docling — platform-independent tests (pc-ct9).

The Windows branch is driven through ``sys.platform`` plus a synthetic Program
Files tree: same code path, synthetic host. A real Program Files lookup is a
local-host observation (memory #1127 — CI cannot see it), so nothing here
depends on the machine it runs on.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from parsecraft.backends.docling import libreoffice as module
from parsecraft.backends.docling.libreoffice import (
    LIBREOFFICE_ENV,
    LibreOfficeUnavailableError,
    configure_libreoffice_env,
    find_libreoffice_cmd,
    resolve_libreoffice_cmd,
)


@pytest.fixture(autouse=True)
def _isolate_libreoffice_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """This module must neither inherit nor leak a declared DOCLING_LIBREOFFICE_CMD.

    ``configure_libreoffice_env()`` writes the process environment on purpose —
    that is docling's contract — so the variable outlives the call. The teardown
    pop keeps this module's state out of the rest of the suite (and the delenv
    keeps another module's state out of this one).
    """
    monkeypatch.delenv(LIBREOFFICE_ENV, raising=False)
    yield
    os.environ.pop(LIBREOFFICE_ENV, None)


# ── 1. The operator's declaration wins, verbatim and unprobed ──────────────


def test_declared_env_var_wins_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """A declared path is a decision: used exactly as given, existence never checked."""
    declared = r"C:\somewhere\soffice.exe"  # deliberately does not exist
    monkeypatch.setenv(LIBREOFFICE_ENV, declared)
    assert find_libreoffice_cmd() == declared
    assert resolve_libreoffice_cmd() == declared


def test_blank_env_var_is_not_a_declaration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace is not a path — discovery continues instead of returning ''."""
    monkeypatch.setenv(LIBREOFFICE_ENV, "   ")
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert find_libreoffice_cmd() is None


# ── 2. Windows: bounded Program Files scan ────────────────────────────────


def test_windows_finds_the_standard_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(module.sys, "platform", "win32")
    program_files = tmp_path / "Program Files"
    expected = _soffice(program_files, "LibreOffice")
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "Program Files (x86)"))
    assert find_libreoffice_cmd() == str(expected)


def test_windows_finds_a_versioned_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Old installs keep the version in the directory name (LibreOffice 5.4)."""
    monkeypatch.setattr(module.sys, "platform", "win32")
    program_files = tmp_path / "Program Files"
    expected = _soffice(program_files, "LibreOffice 5.4")
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    assert find_libreoffice_cmd() == str(expected)


def test_windows_prefers_the_standard_over_a_versioned_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Deterministic precedence: the plain directory wins over any versioned one."""
    monkeypatch.setattr(module.sys, "platform", "win32")
    program_files = tmp_path / "Program Files"
    _soffice(program_files, "LibreOffice 5.4")
    standard = _soffice(program_files, "LibreOffice")
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    assert find_libreoffice_cmd() == str(standard)


def test_windows_reports_absent_when_nothing_is_installed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    assert find_libreoffice_cmd() is None


def test_windows_never_guesses_a_drive_letter(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Program Files variables at all → no scan, no guessed default path."""
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    assert find_libreoffice_cmd() is None


def test_windows_falls_through_to_the_x86_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A 32-bit Python sees the 64-bit install only through ProgramFiles(x86)."""
    monkeypatch.setattr(module.sys, "platform", "win32")
    x86 = tmp_path / "Program Files (x86)"
    expected = _soffice(x86, "LibreOffice")
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.setenv("ProgramFiles(x86)", str(x86))
    assert find_libreoffice_cmd() == str(expected)


def _soffice(root: Path, *parts: str) -> Path:
    """Create a fake soffice.exe under ``root`` and return its path."""
    target = root.joinpath(*parts, "program", "soffice.exe")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"")
    return target


# ── 3. POSIX: PATH only ───────────────────────────────────────────────────


def test_posix_uses_path_lookup_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No filesystem scan on POSIX: Program Files is irrelevant, PATH decides."""
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))  # must be ignored
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/soffice" if name == "soffice" else None)
    assert find_libreoffice_cmd() == "/usr/bin/soffice"


def test_posix_accepts_the_libreoffice_binary_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Some distributions install only the ``libreoffice`` wrapper."""
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/libreoffice" if name == "libreoffice" else None)
    assert find_libreoffice_cmd() == "/usr/bin/libreoffice"


def test_posix_reports_absent_when_path_has_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert find_libreoffice_cmd() is None


# ── 4. Typed unavailability ───────────────────────────────────────────────


def test_resolve_raises_a_typed_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LIBREOFFICE_ENV, raising=False)
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(LibreOfficeUnavailableError) as excinfo:
        resolve_libreoffice_cmd()
    message = str(excinfo.value)
    assert "soffice" in message
    assert LIBREOFFICE_ENV in message  # actionable: names the override


# ── 5. Wiring docling reads ───────────────────────────────────────────────


def test_configure_sets_the_env_var_docling_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/soffice" if name == "soffice" else None)
    assert configure_libreoffice_env() is True
    assert os.environ[LIBREOFFICE_ENV] == "/usr/bin/soffice"


def test_configure_never_overwrites_an_operator_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LIBREOFFICE_ENV, "/opt/mine/soffice")
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/soffice" if name == "soffice" else None)
    assert configure_libreoffice_env() is True
    assert os.environ[LIBREOFFICE_ENV] == "/opt/mine/soffice"


def test_configure_is_quiet_when_there_is_no_libreoffice(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absence is not a conversion failure: no declared format needs LibreOffice."""
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert configure_libreoffice_env() is False
    assert LIBREOFFICE_ENV not in os.environ
