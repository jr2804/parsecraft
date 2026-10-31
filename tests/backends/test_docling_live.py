"""Live docling-rs tier: real engine, real model caches (pc-04u).

Opt-in (``--run-live-docling``); never runs in CI — these tests need the
1.4 GB model caches and exist to verify the adapter against the real
engine on a prepared host. Every conversion runs in its own short-lived
child process: docling-rs shares loaded models with other live
docling-rs processes (volatile, kernel-scoped — measured: a parent that
has converted makes a fresh child with a partial-cache ``DOCLING_RS_CACHE_DIR``
still OCR from the shared models; once the parent exits, the same child
warns), so this process only orchestrates and never imports the engine
(the engine import lives in the child code only — a function-local import
here would be hoisted to module level by pyreorder and break collection
on hosts without the engine).

- pass case (``test_scan_ocrs_when_models_are_present``): default cache,
  full model set — an image-only PDF must come back with real OCR text
  (the first evidence the OCR path works through the adapter at all).
  Skips on the typed missing-models failure when no cache was downloaded.
- warning case (``test_scan_warns_when_only_ocr_models_are_missing``): a
  partial cache with the layout models present and the ``ocr_*`` models
  removed — the page must carry the typed ``docling-ocr-unavailable``
  warning instead of failing silently.

Partial-cache recipe (from the pc-04u isolation): copy a full model cache
and delete only the ``ocr_*`` files, then point
``PARSECRAFT_DOCLING_PARTIAL_CACHE`` at the copy's parent directory
(``DOCLING_RS_CACHE_DIR`` semantics). Nothing here downloads anything.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from typing import cast

import pytest

#: Collection-safe engine probe: ``find_spec`` answers without importing.
#: A module-level ``pytest.importorskip`` cannot be used here — it raises
#: ``Skipped``, which the collection-safety sweep records as an ERROR (no
#: heavy import happened, so the sweep has no violation to attribute it to).
_HAVE_ENGINE = importlib.util.find_spec("docling_rs") is not None

_PARTIAL_CACHE_ENV = "PARSECRAFT_DOCLING_PARTIAL_CACHE"

# Runs in a child process (see module docstring). The caller prepends an
# optional cache-redirect line before any docling import binds it.
_CHILD_CODE = """
import io, json
from PIL import Image, ImageDraw
from parsecraft.backends.docling import docling as docling_module
from parsecraft.backends.protocol import BackendConfig, ConversionRequest, SourceDocument
img = Image.new("RGB", (1240, 1754), "white")
draw = ImageDraw.Draw(img)
for index in range(3):
    draw.text((80, 120 + index * 110), f"SCANNED LINE {index + 1}: THE QUICK BROWN FOX", fill="black")
buf = io.BytesIO(); img.save(buf, format="PDF", resolution=150)
source = SourceDocument(uri="file:///scan.pdf", media_type="application/pdf", content=buf.getvalue())
backend = docling_module.factory(BackendConfig(name="docling"))
result = backend.convert(ConversionRequest(source=source))
page = result.pages[0]
print("VERDICT " + json.dumps({
    "failure_messages": [f.message for f in result.failures],
    "failures": [f.code.value for f in result.failures],
    "blocks": len(page.blocks),
    "text": " ".join(block.content for block in page.blocks),
    "codes": [d.code for d in page.diagnostics],
    "message": next((d.message for d in page.diagnostics if d.code == "docling-ocr-unavailable"), ""),
}))
"""

_PARTIAL_CACHE_PREFIX = 'import os\nos.environ["DOCLING_RS_CACHE_DIR"] = os.environ["PARSECRAFT_DOCLING_PARTIAL_CACHE"]\n'

pytestmark = [
    pytest.mark.docling_live,
    pytest.mark.skipif(not _HAVE_ENGINE, reason="docling-live tier needs the real docling-rs engine"),
]


def test_scan_ocrs_when_models_are_present() -> None:
    """Full cache: an image-only PDF comes back with real OCR text."""
    verdict = _run_child("")
    failure_messages = cast("list[str]", verdict["failure_messages"])
    if any("docling_rs.download_models()" in message for message in failure_messages):
        pytest.skip("no docling model cache (run docling_rs.download_models() once)")
    failures = cast("list[str]", verdict["failures"])
    text = cast("str", verdict["text"])
    assert failures == []
    assert verdict["blocks"], "models present: the scan must OCR, not come back empty"
    assert verdict["codes"] == []
    assert "QUICK BROWN FOX" in text.upper()


def test_scan_warns_when_only_ocr_models_are_missing() -> None:
    """Partial cache (layout present, ocr_* deleted): typed page warning, not silence."""
    partial = os.environ.get(_PARTIAL_CACHE_ENV)
    if not partial:
        pytest.skip(f"set {_PARTIAL_CACHE_ENV} to a prepared partial model cache (see module docstring)")
    verdict = _run_child(_PARTIAL_CACHE_PREFIX)
    codes = cast("list[str]", verdict["codes"])
    message = cast("str", verdict["message"])
    assert verdict["failures"] == []  # the engine continues; this is a warning, not an error
    assert verdict["blocks"] == 0  # the degraded outcome itself is unchanged
    assert "docling-ocr-unavailable" in codes
    assert "docling_rs.download_models()" in message


def _run_child(prefix: str) -> dict[str, object]:
    code = prefix + textwrap.dedent(_CHILD_CODE)
    proc = subprocess.run(  # noqa: S603 - trusted local interpreter, fixed argv, no shell
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    verdict_line = next(line for line in proc.stdout.splitlines() if line.startswith("VERDICT "))
    return json.loads(verdict_line[len("VERDICT ") :])
