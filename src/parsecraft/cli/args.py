"""CLI arguments, options, and flags for ParseCraft."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

# Application name for environment variables
APP_NAME_UPPERCASE = "PARSECRAFT"

# Global options

JsonFlag = Annotated[
    bool,
    typer.Option(
        "--json",
        help="Emit machine-readable JSON",
        envvar=f"{APP_NAME_UPPERCASE}_JSON",
    ),
]

#: Path to an explicit project config file (overrides ``./parsecraft.toml``).
ConfigFileOption = Annotated[
    Path | None,
    typer.Option(
        "--config-file",
        help="Project config file to load instead of ./parsecraft.toml",
    ),
]

#: Opt out of the third-party quieting: show library logs and progress bars.
VerboseFlag = Annotated[
    bool,
    typer.Option(
        "--verbose",
        help="Do not suppress third-party library output (progress bars, tokenizer and generation advisories)",
    ),
]
