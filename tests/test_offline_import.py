"""Import contract: offline, no heavyweight runtimes, no network at import."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

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
    import parsecraft.backends.marker
    import parsecraft.backends.marker.marker
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
            "marker",
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


_COLLECTION_SCRIPT = textwrap.dedent(
    """
    import importlib.abc
    import importlib.machinery
    import importlib.util
    import pathlib
    import sys

    # Import names of the optional extras the canonical light env does NOT
    # install (download / web / pdf-lite ARE installed, so huggingface_hub and
    # pypdf stay importable), plus the runtime leaves those extras pull in.
    # A test module may reach these at call time only — never at collection:
    # pyreorder's hoist_inline_imports promotes an inline import to module
    # scope before the gate runs, so "inline" is not a safe haven.
    BLOCKED = {
        "marker",
        "docling",
        "liteparse",
        "mineru",
        "pypandoc",
        "pdf_inspector",
        "pymupdf",
        "transformers",
        "torch",
        "torchvision",
        "vllm",
        "pypdfium2",
        "rapidocr",
        "typesafe_sdk",
    }

    imported = []

    class _BlockedLoader(importlib.abc.Loader):
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            imported.append(module.__name__)
            raise ModuleNotFoundError(f"{module.__name__!r} is blocked for collection safety")

    class _BlockFinder:
        @classmethod
        def find_spec(cls, fullname, path=None, target=None):
            if fullname.split(".", 1)[0] in BLOCKED:
                # A spec whose LOADER raises: find_spec() probes (skipif guards,
                # EXTRA_IMPORTS detection) still see the name, while a real
                # import dies at execution — which is where "needed at
                # collection" is recorded.
                return importlib.util.spec_from_loader(fullname, _BlockedLoader())
            return None

    sys.meta_path.insert(0, _BlockFinder)

    violations = {}
    errors = {}
    paths = sorted(pathlib.Path("tests").glob("test_*.py"))
    for path in paths:
        before = len(imported)
        spec = importlib.util.spec_from_file_location(f"_collection_safety_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except ModuleNotFoundError:
            if len(imported) > before:
                violations[path.name] = imported[before:]
            else:
                errors[path.name] = "ModuleNotFoundError: " + str(sys.exc_info()[1])
        except ImportError as exc:
            if len(imported) > before:
                violations[path.name] = imported[before:]
            else:
                errors[path.name] = f"{type(exc).__name__}: {exc}"
        except BaseException as exc:  # noqa: BLE001 - any collection-time crash is a finding
            errors[path.name] = f"{type(exc).__name__}: {exc}"

    for name, names in sorted(violations.items()):
        print(f"VIOLATION {name} imports {', '.join(sorted(set(names)))} at collection")
    for name, reason in sorted(errors.items()):
        print(f"ERROR {name}: {reason}")
    if violations or errors:
        raise SystemExit(1)
    print(f"COLLECTION_OK {len(paths)}")
    """,
)


def test_every_test_module_collects_without_heavy_packages() -> None:
    """No test module may need a heavy dep to be *imported* (pc-eo7).

    The mechanical half of "code must survive pyreorder": the format step
    hoists function-local imports, so an inline heavy import in a test file
    becomes a collection-time import before the gate's test step runs — the
    marker suite's regression (memory #1474). Every ``tests/test_*.py`` is
    re-imported from a clean subprocess with the sanctioned heavy set blocked
    through a ``sys.meta_path`` finder, and none of them may touch it.
    """
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _COLLECTION_SCRIPT],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    marker = next((line for line in proc.stdout.splitlines() if line.startswith("COLLECTION_OK")), None)
    assert marker is not None, f"no COLLECTION_OK marker: {proc.stdout}"
    checked = int(marker.split()[1])
    expected = len(list(Path(__file__).parent.glob("test_*.py")))
    assert checked == expected, f"sweep checked {checked} of {expected} test modules — it did not see them all"
