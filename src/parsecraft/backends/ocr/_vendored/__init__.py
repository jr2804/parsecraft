"""Vendored, minimally patched copies of model-card remote code.

Source: HF repo files at the descriptor's pinned revision — every file is
byte-verified against ``ModelAssetDescriptor.file_pins`` at vendoring time
(sha256 recorded in each file's provenance header).  Loaded explicitly by
the OCR impls instead of ``trust_remote_code`` so a unified transformers
window works (pc-4u7.36). See ``backends/ocr/AGENTS.md``.
"""

from __future__ import annotations
