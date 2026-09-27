"""CLI command implementations for ParseCraft."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from parsecraft.backends import default_registry
from parsecraft.cli import args, config
from parsecraft.cli.convert import ConvertError, convert_source, render
from parsecraft.config import ConfigError

# ═══════════════════════════════════════════════════════════════════════════
# Commands are plain top-level functions; registration happens in app.py
# (`app.command()(commands.your_cmd)`). Do NOT add @app.command() decorators
# here and do NOT import `app` from app.py: that creates a circular import
# and makes clean-sort reorder this module into a broken state.
# ═══════════════════════════════════════════════════════════════════════════


def backends(as_json: args.JsonFlag = False) -> None:
    """List registered document backends."""
    descriptors = default_registry.list_backends()
    for name, error in sorted(default_registry.load_errors.items()):
        typer.echo(f"warning: backend {name!r} failed to load: {error}", err=True)
    if as_json:
        payload = [descriptor.model_dump(mode="json") for descriptor in descriptors]
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    if not descriptors:
        typer.echo("No backends registered.")
        return
    for descriptor in descriptors:
        caps = descriptor.capabilities
        formats = ",".join(caps.supported_formats) or "-"
        device = "gpu" if caps.requires_gpu else "cpu"
        vram = f" vram<={caps.estimated_vram_gb:g}G" if caps.estimated_vram_gb is not None else ""
        typer.echo(f"{descriptor.name:24} {device}{vram:12} {formats}")


def config_check(as_json: args.JsonFlag = False, config_file: args.ConfigFileOption = None) -> None:
    """Validate the effective ParseCraft configuration."""
    try:
        engine = config.build_engine(config_file)
    except ConfigError as exc:
        _emit_config_error(exc, as_json=as_json)
        raise typer.Exit(code=1) from exc
    report = engine.check()
    if as_json:
        typer.echo(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        for is_error, line in config.check_lines(report):
            typer.echo(line, err=is_error)
    if not report.valid:
        raise typer.Exit(code=1)


def config_show(as_json: args.JsonFlag = False, config_file: args.ConfigFileOption = None) -> None:
    """Show the resolved effective configuration with per-key provenance."""
    try:
        engine = config.build_engine(config_file)
        entries = config.resolved_entries(engine)
    except ConfigError as exc:
        _emit_config_error(exc, as_json=as_json)
        raise typer.Exit(code=1) from exc
    if as_json:
        typer.echo(json.dumps([entry.model_dump(mode="json") for entry in entries], indent=2, sort_keys=True))
        return
    for line in config.show_lines(entries):
        typer.echo(line)


def _emit_config_error(error: ConfigError, *, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps({"error": str(error)}, indent=2, sort_keys=True))
        return
    typer.echo(f"error: {error}", err=True)


def convert(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True, help="Source document to convert")],
    *,
    backend: Annotated[str | None, typer.Option("--backend", "-b", help="Non-auto: prefer this backend")] = None,
    auto: Annotated[bool, typer.Option("--auto/--no-auto", help="Route automatically (default); --no-auto requires --backend")] = True,
    as_json: args.JsonFlag = False,
    max_passes: Annotated[int, typer.Option("--max-passes", min=1, help="Fallback passes per page group")] = 1,
    no_ocr: Annotated[bool, typer.Option("--no-ocr", help="Forbid OCR backends")] = False,
) -> None:
    """Convert a document through the auto-mode pipeline."""
    if not auto and backend is None:
        typer.echo("error: --no-auto requires --backend", err=True)
        raise typer.Exit(code=2)
    try:
        document = convert_source(source, backend=backend, max_passes=max_passes, allow_ocr=False if no_ocr else None)
    except ConvertError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=exc.exit_code) from exc
    typer.echo(render(document, as_json=as_json))
