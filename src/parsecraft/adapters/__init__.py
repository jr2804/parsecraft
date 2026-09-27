"""Adapters: parse external inputs (Markdown, HTML, ...) into the canonical IR.

INPUT parsing only — the IR is the single source of truth; adapters never
read back rendered projections (see ``parsecraft.ir.AGENTS.md``).
"""

from __future__ import annotations

from parsecraft.adapters.encoding import EncodingDetection, decode, parse_content_type
from parsecraft.adapters.errors import AdapterError, HttpFetchError, MissingDependencyError
from parsecraft.adapters.http import HttpResource, fetch
from parsecraft.adapters.markdown import parse_markdown

__all__ = [
    "AdapterError",
    "EncodingDetection",
    "HttpFetchError",
    "HttpResource",
    "MissingDependencyError",
    "decode",
    "fetch",
    "parse_content_type",
    "parse_markdown",
]
