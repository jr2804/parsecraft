"""pdf-inspector backend family — MIT Rust/PyO3 PDF extraction, CPU-only.

Entry point: ``parsecraft.backends.pdf_inspector.pdf_inspector:factory`` (light,
imports no ``pdf_inspector`` code); the heavy ``_impl`` module is pulled in at
instantiation via ``importlib`` — never an inline import (pyreorder hoists those).

The wheel embeds no OCR models, PDFium, or ONNX Runtime, and this backend never
routes pdf-inspector's OCR entry points, so ``analyze``/``convert`` stay offline.
"""

from __future__ import annotations
