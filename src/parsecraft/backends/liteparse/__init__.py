"""LiteParse backend family — Apache-2.0 document parsing, CPU-only.

Entry point: ``parsecraft.backends.liteparse.liteparse:factory`` (light,
imports no ``liteparse`` code); the heavy ``_impl`` module is pulled in at
instantiation via ``importlib`` — never an inline import (pyreorder hoists those).
"""

from __future__ import annotations
