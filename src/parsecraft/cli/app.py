"""Typer CLI app for ParseCraft."""

from __future__ import annotations

from importlib.metadata import version

import typer

from parsecraft.cli import commands

app = typer.Typer(
    name="parsecraft",
    help="Document intelligence: convert any document into typed structured chunks, with Markdown as a deterministic projection",
    add_completion=True,
    no_args_is_help=True,
)

# Register commands. Commands are plain functions in commands.py — no
# @app.command() decorators there, no import of `app`: this avoids the
# circular import (commands.py <-> app.py) and keeps clean-sort from
# re-sorting this file into a broken state. Register with:
# `app.command()(commands.your_command)`.
app.command()(commands.default)
app.command()(commands.backends)

# `config` is a command group. The sub-app is built here (registration home),
# while the commands themselves stay plain functions in commands.py.
config_app = typer.Typer(
    name="config",
    help="Inspect and validate ParseCraft configuration.",
    no_args_is_help=True,
)
config_app.command(name="check")(commands.config_check)
config_app.command(name="show")(commands.config_show)
app.add_typer(config_app, name="config")


@app.callback(invoke_without_command=True)
def _callback(
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        help="Show version and exit",
        is_eager=True,
    ),
) -> None:
    """Document intelligence: convert any document into typed structured chunks, with Markdown as a deterministic projection"""
    if version:
        typer.echo(_get_version())
        raise typer.Exit()


# Version management
def _get_version() -> str:
    """Get application version from package metadata."""
    try:
        return version("parsecraft")
    except Exception:
        return "0.0.0"  # Fallback for development mode


def main() -> None:
    """Entry point for the CLI application."""
    app()
