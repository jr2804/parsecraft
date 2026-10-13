"""The openpyxl backend package (XLSX → typed TABLE chunks).

The light factory lives in :mod:`parsecraft.backends.openpyxl.openpyxl` — the
module the entry point names — so discovery never imports openpyxl. See that
module for the format contract and ``AGENTS.md`` for the local rules.
"""

from __future__ import annotations
