"""Auto-mode executor: analysis entry points and plan → pages dispatch.

``analyze_source(...)`` (public analysis seam: ``MEDIA_TYPES``,
``media_type_for``, ``choose_analyzer``) produces the ``AnalysisResult``
that ``execute(...)`` plans (via :mod:`parsecraft.routing`), dispatches
contiguous page groups to one backend at a time with typed fallbacks, and
aggregates into a :class:`DocumentResult` with trace + failure records.
"""

from __future__ import annotations

from parsecraft.pipeline.analysis import (
    MEDIA_TYPES,
    AnalysisError,
    NoAnalyzerError,
    UnsupportedSourceError,
    analyze_source,
    choose_analyzer,
    media_type_for,
)
from parsecraft.pipeline.executor import ALL_PASSES_FAILED_CODE, CacheProtocol, execute
from parsecraft.pipeline.models import PageGroup, PassAttempt, PipelineResult

__all__ = [
    "ALL_PASSES_FAILED_CODE",
    "MEDIA_TYPES",
    "AnalysisError",
    "CacheProtocol",
    "NoAnalyzerError",
    "PageGroup",
    "PassAttempt",
    "PipelineResult",
    "UnsupportedSourceError",
    "analyze_source",
    "choose_analyzer",
    "execute",
    "media_type_for",
]
