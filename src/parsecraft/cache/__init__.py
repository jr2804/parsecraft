"""Content-addressed conversion cache (keyed ``DocumentResult`` storage).

See ``AGENTS.md`` for the contract; the pipeline seam accepts any
``parsecraft.pipeline.executor.CacheProtocol`` implementation.
"""

from __future__ import annotations

from parsecraft.cache.store import (
    CACHE_SCHEMA_VERSION,
    CacheEntryInfo,
    ConversionCache,
    default_cache_root,
)

__all__ = [
    "CACHE_SCHEMA_VERSION",
    "CacheEntryInfo",
    "ConversionCache",
    "default_cache_root",
]
