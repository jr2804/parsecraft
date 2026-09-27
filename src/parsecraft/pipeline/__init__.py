"""Auto-mode executor: turn a routing plan into executed IR pages.

``execute(...)`` plans (via :mod:`parsecraft.routing`), dispatches
contiguous page groups to one backend at a time with typed fallbacks, and
aggregates a :class:`DocumentResult` with trace + failure records.
"""

from __future__ import annotations

from parsecraft.pipeline.executor import execute
from parsecraft.pipeline.models import PageGroup, PassAttempt, PipelineResult

__all__ = [
    "PageGroup",
    "PassAttempt",
    "PipelineResult",
    "execute",
]
