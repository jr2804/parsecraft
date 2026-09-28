"""``parsecraft inspect`` — per-page analysis signals plus a routing preview."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from parsecraft.backends import default_registry
from parsecraft.backends.errors import BackendError, DependencyUnavailableError, UnsupportedDependencyVersionError
from parsecraft.backends.protocol import AnalysisResult
from parsecraft.backends.registry import BackendRegistry
from parsecraft.cli import convert
from parsecraft.pipeline.analysis import (
    NoAnalyzerError,
    UnsupportedSourceError,
    analyze_source,
    choose_analyzer,
    media_type_for,
)
from parsecraft.routing import RoutingError, RoutingPlan, plan_route


class InspectPreview(BaseModel):
    """Analysis result plus the routing preview, or why it is unavailable."""

    model_config = ConfigDict(frozen=True)

    media_type: str
    analysis: AnalysisResult
    plan: RoutingPlan | None = None
    routing_error: str | None = None


def inspect_source(
    path: Path,
    registry: BackendRegistry = default_registry,
    *,
    max_passes: int = 1,
    allow_ocr: bool | None = None,
) -> InspectPreview:
    """Analyze a source and preview its route without converting content."""
    try:
        media_type = media_type_for(path)
    except UnsupportedSourceError as exc:
        raise convert.ConvertError(str(exc), exit_code=2) from exc
    source = convert.read_source(path, media_type)
    environment = convert.probe_environment()  # a single probe per invocation
    try:
        analyzer = choose_analyzer(registry.list_backends(), media_type, installed_extras=environment.installed_extras)
        analysis = analyze_source(source, registry, media_type=media_type, installed_extras=environment.installed_extras)
    except NoAnalyzerError as exc:
        raise convert.ConvertError(str(exc), exit_code=2) from exc
    except DependencyUnavailableError as exc:
        raise convert.ConvertError(f"optional dependency missing: {exc}") from exc  # exit 1
    except UnsupportedDependencyVersionError as exc:
        raise convert.ConvertError(f"unsupported dependency version: {exc}") from exc  # exit 1
    except BackendError as exc:
        raise convert.ConvertError(f"analysis with {analyzer.name!r} failed: {exc}") from exc
    if not analysis.signals:
        return InspectPreview(media_type=media_type, analysis=analysis, routing_error="analysis produced no page signals")
    constraints = convert.build_constraints(media_type, max_passes=max_passes, allow_ocr=allow_ocr, environment=environment)
    try:
        plan = plan_route(analysis, registry.list_backends(), constraints)
    except RoutingError as exc:
        return InspectPreview(media_type=media_type, analysis=analysis, routing_error=str(exc))
    return InspectPreview(media_type=media_type, analysis=analysis, plan=plan)


def render_preview(preview: InspectPreview) -> list[str]:
    """Text lines for ``parsecraft inspect``."""
    analysis = preview.analysis
    lines = [f"source: {preview.media_type}", f"hash: {analysis.source_hash}", f"pages: {analysis.page_count}"]
    lines.extend(
        f"page {signal.page_number}: chars={signal.text_chars} images={signal.image_count} "
        f"blank={signal.blank} replacement={'n/a' if signal.replacement_char_ratio is None else f'{signal.replacement_char_ratio:.3f}'}"
        for signal in analysis.signals
    )
    lines.extend(f"diagnostic [{diagnostic.level.value}] {diagnostic.code}: {diagnostic.message}" for diagnostic in analysis.diagnostics)
    if preview.plan is None:
        lines.append(f"routing: unavailable — {preview.routing_error}")
        return lines
    lines.append(f"routing: primary={preview.plan.primary}")
    for route in preview.plan.pages:
        lines.append(f"  page {route.page_number}: intent={route.intent.value} chosen={route.chosen} candidates={','.join(route.candidates)}")
        lines.append(f"    reason: {route.reason}")
    return lines


def preview_payload(preview: InspectPreview) -> dict[str, object]:
    """JSON-ready preview for ``parsecraft inspect --json``."""
    return preview.model_dump(mode="json")
