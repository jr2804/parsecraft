"""Adapters: parse external inputs (Markdown, HTML, ...) into the canonical IR.

INPUT parsing only — the IR is the single source of truth; adapters never
round-trip parsecraft's own rendered Markdown back into IR (see
``parsecraft.ir.AGENTS.md``). ``markdown_blocks`` is the shared Markdown→blocks
primitive: it parses Markdown *text* (a source document, or a backend's rendered
Markdown) and is the output-side twin of ``parse_markdown``.
"""

from __future__ import annotations

from parsecraft.adapters.encoding import EncodingDetection, decode, parse_content_type
from parsecraft.adapters.errors import AdapterError, HttpFetchError, MissingDependencyError
from parsecraft.adapters.http import HttpResource, fetch
from parsecraft.adapters.markdown import markdown_blocks, parse_markdown

__all__ = [
    "AdapterError",
    "EncodingDetection",
    "HttpFetchError",
    "HttpResource",
    "MissingDependencyError",
    "decode",
    "fetch",
    "markdown_blocks",
    "parse_content_type",
    "parse_markdown",
]
