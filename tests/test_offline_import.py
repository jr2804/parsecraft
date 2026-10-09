"""Import contract: offline, no heavyweight runtimes, no network at import."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

#: Runtime roots that must never enter a parsecraft import or a test module's
#: collection. Base dependencies (pypdfium2, promoted by ADR-0008 d10/d14) are
#: deliberately ABSENT — they import at module level by decision; optional
#: extras are PRESENT. The offline gate asserts none of these entered
#: ``sys.modules`` while the core imports; the collection sweep blocks importing
#: them outright.
_HEAVY_RUNTIME_ROOTS: frozenset[str] = frozenset({
    "docling",
    "huggingface_hub",
    "liteparse",
    "marker",
    "mineru",
    "pypandoc",
    "pdf_inspector",
    "pymupdf",
    "rapidocr",
    "trafilatura",
    "torch",
    "torchvision",
    "transformers",
    "typesafe_sdk",
    "vllm",
})

_SCRIPT = textwrap.dedent(
    """
    import socket

    def _blocked(*args, **kwargs):
        raise RuntimeError("network access during import")

    socket.socket.connect = _blocked
    socket.create_connection = _blocked
    socket.getaddrinfo = _blocked

    import importlib.metadata
    import importlib.metadata
    import sys

    HEAVY = __HEAVY_ROOTS__

    # Non-backend core: explicit, so the offline / no-heavy-runtime / no-network
    # contract cannot silently shrink. Backend modules are NOT listed here —
    # they are derived below from the registry entry points.
    core = [
        "parsecraft",
        "parsecraft.adapters",
        "parsecraft.adapters.markdown",
        "parsecraft.assets",
        "parsecraft.assets.downloader",
        "parsecraft.backends",
        "parsecraft.backends.ocr._common",
        "parsecraft.backends.ocr._models",
        "parsecraft.backends.registry",
        "parsecraft.config",
        "parsecraft.ir",
        "parsecraft.ir.markdown",
        "parsecraft.cli.app",
        "parsecraft.environment",
        "parsecraft.environment.constraints",
        "parsecraft.environment.probe",
        "parsecraft.providers",
        "parsecraft.providers._jev",
        "parsecraft.providers.ollama",
        "parsecraft.providers.pdfinspector",
        "parsecraft.providers.typesafe_ai",
        "parsecraft.providers.zen",
        "parsecraft.routing.classifier",
    ]

    # Backends are DERIVED from the registry entry points (pc-khk): every
    # registered factory module plus its package. The hand-maintained list had
    # drifted — trafilatura, mineru, docling, pandoc and the native-* backends
    # were absent, so their import contract was unenforced.
    entry_points = importlib.metadata.entry_points(group="parsecraft.backends")
    factory_modules = set()
    parent_packages = set()
    for ep in entry_points:
        module_path = ep.value.split(":", 1)[0]
        factory_modules.add(module_path)
        parent, _, _ = module_path.rpartition(".")
        if parent:
            parent_packages.add(parent)
    ordered = sorted(factory_modules | parent_packages)
    assert len(entry_points) >= 14, f"backend registry collapsed: {len(entry_points)} entry points"
    assert len(factory_modules) == len(entry_points), (
        f"entry points must map 1:1 to factory modules: {len(entry_points)} entry points -> {len(factory_modules)} modules"
    )
    assert len(ordered) > len(factory_modules), "every backend's package must be part of the import surface too"

    for name in core + ordered:
        __import__(name)

    heavy = [m for m in HEAVY if m in sys.modules]
    assert not heavy, f"heavy runtimes imported at package import: {heavy}"
    heavy_impls = [m for m in sys.modules if m.startswith("parsecraft.backends") and m.endswith("_impl")]
    assert not heavy_impls, f"heavy impl modules imported at package import: {heavy_impls}"
    assert "parsecraft.backends.liteparse._impl" not in sys.modules
    assert "parsecraft.backends.pdf_inspector._impl" not in sys.modules
    print(f"IMPORTS_OK core={len(core)} backends={len(ordered)}")
    """,
).replace("__HEAVY_ROOTS__", repr(sorted(_HEAVY_RUNTIME_ROOTS)))
)


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
    # pypdfium2 and pillow moved to base deps (ADR-0008 d10/d14) — always
    # installed, so a test module importing them at collection is safe.
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
        "rapidocr",
        "trafilatura",
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


def test_package_imports_offline_without_heavy_runtimes() -> None:
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _SCRIPT],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "IMPORTS_OK" in proc.stdout


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
