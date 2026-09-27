"""Typer CLI app for ParseCraft."""

from __future__ import annotations

from importlib.metadata import version

import typer

from parsecraft.cli import commands

app = typer.Typer(
    name="parsecraft",
    help="Document intelligence: align existing converters, parsers, and OCR/VLM models behind one workflow and one typed output format",
    add_completion=True,
    no_args_is_help=True,
)

# Register commands. Commands are plain functions in commands.py — no
# @app.command() decorators there, no import of `app`: this avoids the
# circular import (commands.py <-> app.py) and keeps clean-sort from
# re-sorting this file into a broken state. Register with:
# `app.command()(commands.your_command)`.
app.command()(commands.backends)
app.command()(commands.convert)
app.command()(commands.inspect)
app.command()(commands.benchmark)

# `models` is a command group over the pinned model-asset cache.
models_app = typer.Typer(
    name="models",
    help="Inspect and manage the pinned model-asset cache.",
    no_args_is_help=True,
)
models_app.command(name="list")(commands.models_list)
models_app.command(name="install")(commands.models_install)
models_app.command(name="remove")(commands.models_remove)
models_app.command(name="clean")(commands.models_clean)
models_app.command(name="path")(commands.models_path)
app.add_typer(models_app, name="models")

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
    """Document intelligence: align existing converters, parsers, and OCR/VLM models behind one workflow and one typed output format"""
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
