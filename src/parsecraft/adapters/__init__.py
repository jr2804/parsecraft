"""Adapters: parse external inputs (Markdown, ...) into the canonical IR.

INPUT parsing only — the IR is the single source of truth; adapters never
read back rendered projections (see ``parsecraft.ir.AGENTS.md``).
"""

from __future__ import annotations

from parsecraft.adapters.markdown import parse_markdown

__all__ = ["parse_markdown"]
