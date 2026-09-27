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
    import parsecraft.backends.registry
    import parsecraft.config
    import parsecraft.ir
    import parsecraft.ir.markdown
    import parsecraft.cli.app

    heavy = [
        m
        for m in (
            "torch",
            "transformers",
            "vllm",
            "docling",
            "huggingface_hub",
        )
        if m in sys.modules
    ]
    assert not heavy, f"heavy runtimes imported at package import: {heavy}"
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
