"""Import contract: offline, no heavyweight runtimes, no network at import."""

from __future__ import annotations

import subprocess
import sys
import textwrap

_SCRIPT = textwrap.dedent(
    """
    import socket

    def _blocked(*args, **kwargs):
        raise RuntimeError("network access during import")

    socket.socket.connect = _blocked
    socket.create_connection = _blocked
    socket.getaddrinfo = _blocked

    import sys

    import parsecraft
    import parsecraft.adapters
    import parsecraft.adapters.markdown
    import parsecraft.assets
    import parsecraft.assets.downloader
    import parsecraft.backends
    import parsecraft.backends.ocr
    import parsecraft.backends.ocr._common
    import parsecraft.backends.ocr._models
    import parsecraft.backends.ocr.ovis
    import parsecraft.backends.ocr.qianfan
    import parsecraft.backends.ocr.tele
    import parsecraft.backends.ocr.unlimited
    import parsecraft.backends.liteparse
    import parsecraft.backends.liteparse.liteparse
    import parsecraft.backends.pdf_inspector
    import parsecraft.backends.pdf_inspector.pdf_inspector
    import parsecraft.backends.registry
    import parsecraft.config
    import parsecraft.ir
    import parsecraft.ir.markdown
    import parsecraft.cli.app
    import parsecraft.environment
    import parsecraft.environment.constraints
    import parsecraft.environment.probe
    import parsecraft.providers
    import parsecraft.providers._jev
    import parsecraft.providers.ollama
    import parsecraft.providers.pdfinspector
    import parsecraft.providers.typesafe_ai
    import parsecraft.providers.zen
    import parsecraft.routing.classifier

    heavy = [
        m
        for m in (
            "torch",
            "transformers",
            "vllm",
            "docling",
            "pdf_inspector",
            "typesafe_sdk",
            "huggingface_hub",
        )
        if m in sys.modules
    ]
    assert not heavy, f"heavy runtimes imported at package import: {heavy}"
    heavy_impls = [m for m in sys.modules if m.startswith("parsecraft.backends") and m.endswith("_impl")]
    assert not heavy_impls, f"heavy impl modules imported at package import: {heavy_impls}"
    assert "parsecraft.backends.liteparse._impl" not in sys.modules
    assert "parsecraft.backends.pdf_inspector._impl" not in sys.modules
    print("IMPORTS_OK")
    """,
)


def test_package_imports_offline_without_heavy_runtimes() -> None:
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "IMPORTS_OK" in proc.stdout
