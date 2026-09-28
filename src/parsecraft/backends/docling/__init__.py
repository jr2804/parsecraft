"""Docling backend family — MIT layout-aware parsing (CPU, heavy extra).

Entry point: ``parsecraft.backends.docling.docling:factory`` (light, imports no
``docling`` code); the heavy ``_impl`` module is pulled in at instantiation via
``importlib`` — never an inline import (csort hoists those).
"""

from __future__ import annotations
