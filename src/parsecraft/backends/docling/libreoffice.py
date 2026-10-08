"""LibreOffice discovery for docling's office-format path (pc-ct9).

Docling shells out to LibreOffice for office formats and reads the path from the
``DOCLING_LIBREOFFICE_CMD`` environment variable — that variable IS its contract.
This module resolves that command in the order a host expects:

1. **The operator's ``DOCLING_LIBREOFFICE_CMD``** — taken verbatim, never
   probed. A declared path is a decision, not a fact to second-guess.
2. **Windows**: a bounded scan of the standard install locations
   (``%ProgramFiles%`` and ``%ProgramFiles(x86)%`` → ``LibreOffice*/program/
   soffice.exe``). Bounded on purpose: two roots, one non-recursive directory
   listing each (version-numbered installs like ``LibreOffice 5.4``), never a
   drive-wide walk. No drive-letter guessing — the two environment variables
   are the detection, and a host without them simply reports no LibreOffice.
3. **Everywhere else**: PATH lookup only (``shutil.which``). Linux/macOS
   packaging owns the location; there is nothing to scan.

Detection, never estimation: the result is either the operator's own string or
a path that exists on this host — never a predicted install location. All of it
is stdlib and cheap enough to run once per backend instantiation.

Note on CI and platform coverage (memory #1127): the Windows branch cannot be
exercised on a Linux runner against a real ``Program Files``. The tests here
therefore drive it with a synthetic root through the environment variable and a
temporary tree — the same code path, a synthetic host — and the genuine
Program Files lookup stays a local-host observation.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from parsecraft.backends.errors import BackendError

#: The variable docling itself reads — the whole wiring surface.
LIBREOFFICE_ENV = "DOCLING_LIBREOFFICE_CMD"

#: Windows environment variables holding the two Program Files roots.
_WINDOWS_ROOTS_ENV: tuple[str, ...] = ("ProgramFiles", "ProgramFiles(x86)")

#: Windows executable name (POSIX installs use the names below).
_WINDOWS_EXECUTABLE = "soffice.exe"

#: POSIX command names, most specific first.
_POSIX_COMMANDS: tuple[str, ...] = ("soffice", "libreoffice")


class LibreOfficeUnavailableError(BackendError):
    """No LibreOffice installation could be located on this host."""


def find_libreoffice_cmd() -> str | None:
    """The ``soffice`` command for this host, or ``None`` when there is none."""
    declared = os.environ.get(LIBREOFFICE_ENV, "").strip()
    if declared:  # the operator's declaration wins and is used verbatim
        return declared
    if sys.platform == "win32":
        return _windows_scan()
    return _path_lookup()


def resolve_libreoffice_cmd() -> str:
    """Like :func:`find_libreoffice_cmd`, but a typed failure instead of ``None``."""
    found = find_libreoffice_cmd()
    if found is None:
        msg = f"LibreOffice (soffice) was not found on this host — install it, or set {LIBREOFFICE_ENV} to its path"
        raise LibreOfficeUnavailableError(msg)
    return found


def configure_libreoffice_env() -> bool:
    """Point docling at the discovered LibreOffice; ``False`` when there is none.

    Docling reads ``DOCLING_LIBREOFFICE_CMD`` from the process environment, so
    setting it is the entire integration — no docling API takes the path. Never
    raises: office formats are deliberately NOT in ``supported_formats``, so a
    host without LibreOffice still converts PDF, HTML, Markdown and plain text.
    An operator's own value is never overwritten.
    """
    found = find_libreoffice_cmd()
    if found is None:
        return False
    os.environ.setdefault(LIBREOFFICE_ENV, found)
    return True


def _windows_scan() -> str | None:
    """Bounded Program Files lookup for ``soffice.exe`` (Windows only)."""
    for variable in _WINDOWS_ROOTS_ENV:
        root = os.environ.get(variable, "").strip()
        if not root:
            continue
        base = Path(root)
        standard = base / "LibreOffice" / "program" / _WINDOWS_EXECUTABLE
        if standard.is_file():
            return str(standard)
        # Older installs keep a version in the directory name; one listing, no recursion.
        for versioned in sorted(base.glob("LibreOffice*")):
            candidate = versioned / "program" / _WINDOWS_EXECUTABLE
            if candidate.is_file():
                return str(candidate)
    return None


def _path_lookup() -> str | None:
    """PATH-only lookup for POSIX hosts."""
    for command in _POSIX_COMMANDS:
        found = shutil.which(command)
        if found:
            return found
    return None