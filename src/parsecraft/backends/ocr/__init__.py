"""OCR / document-VLM backend family — light entry points, heavy impls.

Entry-point modules (``ovis``, ``tele``, ``unlimited``, ``qianfan``) stay
import-light: discovery loads them, never the model stack. Each backend's
``_<name>_impl`` partner imports Transformers at module top level by contract
and is pulled in only at instantiation via ``importlib.import_module`` —
never an inline ``import``, which ``csort`` would hoist (backends/AGENTS.md).
"""

from __future__ import annotations
