"""GPU tier: OCR adapters against real weights on a CUDA host (opt-in only).

Runs only with ``pytest --run-gpu`` (``mise run test-corpus``-style gating),
inside the isolated ``.venv-gpu`` environment — never the shared ``.venv``.
Every case converts page 1 of a real PDF from ``tests/downloads/`` and asserts
typed IR comes back, not tensors.

Version matrix validated by the pc-5gf smoke run (2026-09-27, RTX A2000 8 GB):
- ``ocr-ovis`` / ``ocr-qianfan`` → transformers 5.x (5.17 verified)
- ``ocr-tele`` / ``ocr-unlimited`` → transformers 4.57.x (their own pins; both
  ship custom remote code that transformers 5.x rejects)
A warm run takes a few minutes (model loads); the first run downloads weights.
"""

from __future__ import annotations

import gc
import importlib
import importlib.metadata
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from parsecraft.backends.ocr.ovis import factory as ovis_factory
from parsecraft.backends.ocr.qianfan import factory as qianfan_factory
from parsecraft.backends.ocr.tele import factory as tele_factory
from parsecraft.backends.ocr.unlimited import factory as unlimited_factory
from parsecraft.backends.protocol import (
    BackendConfig,
    ConversionRequest,
    DocumentBackend,
    SourceDocument,
)
from parsecraft.ir.models import PageRange

#: (backend name, light factory, required transformers major — from pc-5gf runs).
_GPU_CASES: tuple[tuple[str, Callable[[BackendConfig], DocumentBackend], int], ...] = (
    # All four run on the unified window (pc-4u7.36): tele/unlimited load the
    # vendored modeling in backends/ocr/_vendored — no trust_remote_code.
    ("ocr-ovis", ovis_factory, 5),
    ("ocr-tele", tele_factory, 5),
    ("ocr-qianfan", qianfan_factory, 5),
    ("ocr-unlimited", unlimited_factory, 5),
)

#: Generation caps: small enough for the laptop GPU budget, long enough to prove shape.
_MAX_NEW_TOKENS: dict[str, int] = {
    "ocr-ovis": 256,
    "ocr-tele": 128,
    "ocr-qianfan": 64,
    "ocr-unlimited": 700,
}

pytestmark = pytest.mark.gpu


def test_gpu_environment_is_reported(gpu_report: str) -> None:
    """Record the hardware the smoke run actually used (evidence, not a guess)."""
    torch = importlib.import_module("torch")
    print(f"GPU: {gpu_report}; torch {torch.__version__}; transformers {importlib.metadata.version('transformers')}")
    assert "MiB" in gpu_report


@pytest.fixture(autouse=True)
def _release_vram_between_gpu_tests() -> Iterator[None]:
    """One model resident at a time (8 GB ceiling): drop weights + cached blocks
    after every GPU test so the NEXT case's `device_map="auto"` sees real free
    VRAM (a full session's retained cache otherwise pushes later loads into
    meta/CPU offload — 'Cannot copy out of meta tensor').
    """
    yield
    gc.collect()
    torch_module = sys.modules.get("torch")
    if torch_module is not None and torch_module.cuda.is_available():
        torch_module.cuda.empty_cache()


@pytest.mark.parametrize(("name", "factory", "required_major"), _GPU_CASES)
def test_adapter_converts_a_real_page_on_gpu(
    name: str,
    factory: Callable[[BackendConfig], DocumentBackend],
    required_major: int,
    gpu_report: str,
    real_page_png: bytes,
) -> None:
    installed = _installed_transformers_major()
    if installed != required_major:
        pytest.skip(f"{name} is verified on transformers {required_major}.x, installed is {installed}.x (version matrix from the pc-5gf smoke run)")
    options = {"max_pages_per_call": 1} if name == "ocr-unlimited" else {}
    backend = factory(BackendConfig(name=name, options=options))
    source = SourceDocument(uri="file:///gpu-page.png", content=real_page_png)
    result = backend.convert(ConversionRequest(source=source, page_range=PageRange(start=1, end=1), max_context_tokens=_MAX_NEW_TOKENS[name]))
    assert result.failures == [], f"{name} typed failures: {[(f.code, f.detail) for f in result.failures]}"
    assert [page.page_number for page in result.pages] == [1]
    content = result.pages[0].blocks[0].content
    assert content.strip(), f"{name} returned an empty page"
    assert not content.startswith("<|im_start|>"), f"{name} echoed the chat template (strip broken)"
    assert result.backend.model_id

    torch = importlib.import_module("torch")
    print(f"{name}: {len(content)} chars; VRAM peak {torch.cuda.max_memory_allocated() // 2**20} MiB on {gpu_report}")


def _installed_transformers_major() -> int:
    """Major version of transformers in THIS environment (metadata only, no import)."""
    return int(importlib.metadata.version("transformers").split(".", 1)[0])


@pytest.fixture(scope="module")
def gpu_report() -> str:
    """CUDA device summary; skips cleanly when the host has no GPU."""
    torch = importlib.import_module("torch")
    if not torch.cuda.is_available():
        pytest.skip("CUDA is not available on this host")
    properties = torch.cuda.get_device_properties(0)
    return f"{properties.name} / {properties.total_memory // 2**20} MiB"


@pytest.fixture(scope="module")
def real_page_png() -> bytes:
    """Render page 1 of the first PDF in tests/downloads at 150 dpi."""
    pdfs = sorted(Path("tests/downloads").glob("*.pdf"))
    if not pdfs:
        pytest.skip("no PDF in tests/downloads — see tests/fixtures/sources.toml for the local step")
    pymupdf = importlib.import_module("pymupdf")
    document = pymupdf.open(stream=pdfs[0].read_bytes(), filetype="pdf")
    try:
        return document.load_page(0).get_pixmap(dpi=150).tobytes("png")
    finally:
        document.close()
