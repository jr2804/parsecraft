"""Shared CLI error type: a message plus the process exit code to use."""

from __future__ import annotations


class CliError(Exception):
    """A command failure that maps to a CLI exit code (default ``1``)."""

    def __init__(self, detail: str, *, exit_code: int = 1) -> None:
        super().__init__(detail)
        self.exit_code = exit_code
