"""CLI command implementations for ParseCraft."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from parsecraft.backends import default_registry
from parsecraft.backends.protocol import GPU_NOT_NEEDED, GPU_REQUIRED, BackendCapabilities
from parsecraft.cache import ConversionCache
from parsecraft.cli import args, config, verbosity
from parsecraft.cli import benchmark as benchmark_module
from parsecraft.cli import inspect as inspect_module
from parsecraft.cli import judges as judges_module
from parsecraft.cli import models as models_module
from parsecraft.cli.convert import ConvertError, convert_source, render
from parsecraft.cli.errors import CliError
from parsecraft.config import ConfigError
from parsecraft.routing import RoutingPreference

# ═══════════════════════════════════════════════════════════════════════════
# Commands are plain top-level functions; registration happens in app.py
# (`app.command()(commands.your_cmd)`). Do NOT add @app.command() decorators
# here and do NOT import `app` from app.py: that creates a circular import
# and makes pyreorder reorder this module into a broken state.
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
        device = _device_label(caps)
        vram = f" vram<={caps.estimated_vram_gb:g}G" if caps.estimated_vram_gb is not None else ""
        typer.echo(f"{descriptor.name:24} {device}{vram:12} {formats}")


def _device_label(capabilities: BackendCapabilities) -> str:
    """The device column: hard-GPU, either, or CPU-only (``gpu_requirement``)."""
    if capabilities.gpu_requirement >= GPU_REQUIRED:
        return "gpu"
    if capabilities.gpu_requirement > GPU_NOT_NEEDED:
        return "cpu/gpu"
    return "cpu"


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
    judge: Annotated[
        str | None,
        typer.Option(
            "--judge",
            help=(
                "Route with a judge provider (`provider/model[:variant]`); mutually exclusive with --backend; "
                "combinable with --classifier; may use the network. Run `parsecraft judges` for the built-in "
                "providers and their model tokens"
            ),
        ),
    ] = None,
    classifier: Annotated[
        str | None,
        typer.Option("--classifier", help="Fold OCR-need facts from a classifier provider (`provider/model[:variant]`) into the analysis"),
    ] = None,
    auto: Annotated[bool, typer.Option("--auto/--no-auto", help="Route automatically (default); --no-auto requires --backend")] = True,
    preference: Annotated[
        RoutingPreference,
        typer.Option("--preference", help="Ranking axis inside the eligible family: speed, balanced, or quality"),
    ] = RoutingPreference.BALANCED,
    as_json: args.JsonFlag = False,
    max_passes: Annotated[int, typer.Option("--max-passes", min=1, help="Fallback passes per page group")] = 1,
    no_ocr: Annotated[bool, typer.Option("--no-ocr", help="Forbid OCR backends")] = False,
    use_cache: Annotated[bool, typer.Option("--cache/--no-cache", help="Reuse a content-addressed conversion cache")] = False,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", dir_okay=False, help="Write the result to PATH (UTF-8) instead of stdout; the directory must already exist"),
    ] = None,
    verbose: args.VerboseFlag = False,
) -> None:
    """Convert a document through the auto-mode pipeline."""
    if not auto and backend is None:
        typer.echo("error: --no-auto requires --backend", err=True)
        raise typer.Exit(code=2)
    if output is not None and not output.parent.is_dir():
        # Fail fast as a usage error: never spend minutes converting into a path that cannot be written.
        typer.echo(f"error: output directory does not exist: {output.parent} (create it first)", err=True)
        raise typer.Exit(code=2)
    try:
        with verbosity.third_party_output(verbose=verbose):
            document = convert_source(
                source,
                backend=backend,
                judge=judge,
                classifier=classifier,
                max_passes=max_passes,
                allow_ocr=False if no_ocr else None,
                preference=preference,
                use_cache=use_cache,
            )
    except ConvertError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=exc.exit_code) from exc
    rendered = render(document, as_json=as_json)
    if output is None:
        typer.echo(rendered)
        return
    try:
        output.write_text(f"{rendered}\n", encoding="utf-8")  # byte-identical to what typer.echo prints above
    except OSError as exc:
        typer.echo(f"error: cannot write {output}: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def inspect(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True, help="Source document to analyze")],
    *,
    as_json: args.JsonFlag = False,
    max_passes: Annotated[int, typer.Option("--max-passes", min=1, help="Fallback passes per page group")] = 1,
    no_ocr: Annotated[bool, typer.Option("--no-ocr", help="Forbid OCR backends")] = False,
    verbose: args.VerboseFlag = False,
) -> None:
    """Analyze a source and preview the routing decision."""
    try:
        with verbosity.third_party_output(verbose=verbose):
            preview = inspect_module.inspect_source(source, default_registry, max_passes=max_passes, allow_ocr=False if no_ocr else None)
    except CliError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=exc.exit_code) from exc
    if as_json:
        typer.echo(json.dumps(inspect_module.preview_payload(preview), indent=2, sort_keys=True))
        return
    for line in inspect_module.render_preview(preview):
        typer.echo(line)


def judges(as_json: args.JsonFlag = False) -> None:
    """List the built-in judge providers and the model tokens they accept."""
    items = judges_module.entries()
    if as_json:
        typer.echo(json.dumps(judges_module.entries_payload(items), indent=2, sort_keys=True))
        return
    for line in judges_module.render_entries(items):
        typer.echo(line)


def models_list(as_json: args.JsonFlag = False) -> None:
    """List registered model assets and local cache state."""
    items = models_module.entries(models_module.available_descriptors(), models_module.manager().inspect_cache())
    if as_json:
        typer.echo(json.dumps(models_module.entries_payload(items), indent=2, sort_keys=True))
        return
    for line in models_module.render_entries(items):
        typer.echo(line)


def models_path(as_json: args.JsonFlag = False) -> None:
    """Print the local model cache location and size."""
    report = models_module.manager().inspect_cache()
    if as_json:
        typer.echo(json.dumps({"location": report.location, "total_bytes": report.total_bytes}, indent=2, sort_keys=True))
        return
    typer.echo(f"{report.location} ({models_module.human_bytes(report.total_bytes)})")


def models_install(
    name: Annotated[str, typer.Argument(help="Backend name or model id to install")],
    *,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Confirm the network download")] = False,
    accept_license: Annotated[bool, typer.Option("--accept-license", help="Accept the model license terms")] = False,
) -> None:
    """Download and verify a pinned model asset (requires the download extra)."""
    try:
        descriptor = models_module.resolve_asset(name, models_module.available_descriptors())[1]
        paths = models_module.install(descriptor, models_module.manager(), confirmed=yes, accept_license=accept_license)
    except CliError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=exc.exit_code) from exc
    for path in paths:
        typer.echo(f"installed {path}")


def models_remove(name: Annotated[str, typer.Argument(help="Backend name or model id to remove")]) -> None:
    """Delete every cached revision of one model."""
    try:
        descriptor = models_module.resolve_asset(name, models_module.available_descriptors())[1]
    except CliError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=exc.exit_code) from exc
    removed = models_module.remove(descriptor, models_module.manager())
    if not removed:
        typer.echo(f"{descriptor.model_id} is not cached.")
        return
    for revision in removed:
        typer.echo(f"removed {descriptor.model_id}@{revision}")


def models_clean() -> None:
    """Delete every cached model revision."""
    removed = models_module.clean(models_module.manager())
    typer.echo(f"removed {removed} cached revision(s)")


def cache(
    as_json: args.JsonFlag = False,
    *,
    clear: Annotated[bool, typer.Option("--clear", help="Delete every cached conversion")] = False,
) -> None:
    """Inspect or clear the content-addressed conversion cache."""
    store = ConversionCache()
    if clear:
        removed = store.clear()
        typer.echo(f"removed {removed} conversion cache entries from {store.root}")
        return
    entries = store.entries()
    if as_json:
        payload = {
            "location": str(store.root),
            "entries": len(entries),
            "total_bytes": store.total_bytes(),
            "keys": [entry.key for entry in entries],
        }
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"location: {store.root}")
    typer.echo(f"entries: {len(entries)}")
    typer.echo(f"size: {store.total_bytes()} bytes")


def benchmark(
    paths: Annotated[list[Path], typer.Argument(exists=True, dir_okay=False, readable=True, help="Local documents to benchmark")],
    *,
    as_json: args.JsonFlag = False,
    markdown: Annotated[bool, typer.Option("--markdown", help="Force the Markdown report (the default)")] = False,
    output: Annotated[Path | None, typer.Option("--output", "-o", file_okay=False, help="Directory for benchmark.json and benchmark.md")] = None,
    max_passes: Annotated[int, typer.Option("--max-passes", min=1, help="Fallback passes per page group")] = 1,
    no_ocr: Annotated[bool, typer.Option("--no-ocr", help="Forbid OCR backends")] = False,
    verbose: args.VerboseFlag = False,
) -> None:
    """Benchmark eligible backends over local documents (offline)."""
    if as_json and markdown:
        typer.echo("error: --json and --markdown are mutually exclusive", err=True)
        raise typer.Exit(code=2)
    try:
        with verbosity.third_party_output(verbose=verbose):
            report = benchmark_module.benchmark_report(paths, max_passes=max_passes, allow_ocr=False if no_ocr else None)
            written = benchmark_module.write_reports(report, output) if output is not None else []
    except CliError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=exc.exit_code) from exc
    for path in written:
        typer.echo(f"wrote {path}", err=True)
    typer.echo(benchmark_module.render(report, as_json=as_json))
