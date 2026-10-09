"""MinerU backend family — layout-aware PDF parsing behind the `mineru` extra.

Entry point: ``parsecraft.backends.mineru.mineru:factory`` (light, imports no
``mineru`` code); the heavy ``_impl`` module is pulled in at instantiation via
``importlib``.
"""

from __future__ import annotations
