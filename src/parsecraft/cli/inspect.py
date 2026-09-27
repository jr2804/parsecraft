"""``parsecraft inspect`` — per-page analysis signals plus a routing preview."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from parsecraft.backends import default_registry
from parsecraft.backends.protocol import AnalysisResult
from parsecraft.backends.registry import BackendRegistry
from parsecraft.cli import convert
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
    media_type = convert.media_type_for(path)
    source = convert.read_source(path, media_type)
    analysis = convert.analyze(registry, source, media_type)
    if not analysis.signals:
        return InspectPreview(media_type=media_type, analysis=analysis, routing_error="analysis produced no page signals")
    constraints = convert.build_constraints(media_type, max_passes=max_passes, allow_ocr=allow_ocr)
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
