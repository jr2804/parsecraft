"""Third-party backend: entry-point registration without touching our source.

Proves the plan's core extension claim end to end using the real example
package under ``examples/third_party_backend`` (separate distribution, its own
``pyproject.toml``, ``parsecraft.backends`` entry point).
"""

from __future__ import annotations

import importlib
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

from parsecraft.backends import (
    BackendConfig,
    BackendRegistry,
    ConversionRequest,
    SourceDocument,
)
from parsecraft.backends import registry as registry_module
from parsecraft.ir.models import PageRange

_EXAMPLE_ROOT = Path("examples/third_party_backend")
_EXAMPLE_SRC = _EXAMPLE_ROOT / "src"
_MODULE_NAME = "parsecraft_example_backend"
_IMPL_MODULE = f"{_MODULE_NAME}.impl"


class _EntryPoint:
    def __init__(self, name: str, target: str) -> None:
        self.name = name
        self._target = target

    def load(self) -> object:
        # Mirror importlib.metadata semantics: "module:attr".
        module_name, _, attr = self._target.partition(":")
        module = importlib.import_module(module_name)
        return getattr(module, attr)


def test_entry_point_discovery_is_lazy_and_works(
    monkeypatch: pytest.MonkeyPatch,
    example_on_path: None,
) -> None:
    registry = _registry_with_example(monkeypatch)
    descriptors = registry.list_backends()
    assert [d.name for d in descriptors] == ["example-echo"]
    # Discovery imported only the light entry-point module.
    assert _IMPL_MODULE not in sys.modules
    assert registry.load_errors == {}

    backend = registry.create("example-echo", BackendConfig(name="example-echo"))
    # Instantiation — the sanctioned heavy-import boundary — pulled impl in.
    assert _IMPL_MODULE in sys.modules

    result = backend.convert(
        ConversionRequest(
            source=SourceDocument(uri="file:///x.txt", content=b"hi"),
            page_range=PageRange(start=2, end=3),
        )
    )
    assert [page.page_number for page in result.pages] == [2, 3]
    assert result.pages[0].blocks[0].content == "echo page 2"
    assert result.backend.name == "example-echo"

    analysis = backend.analyze(SourceDocument(uri="file:///x.txt", content=b"hi"))
    assert len(analysis.source_hash) == 64
    assert analysis.signals[0].has_native_text is True


def _registry_with_example(monkeypatch: pytest.MonkeyPatch) -> BackendRegistry:
    point = _EntryPoint("example-echo", f"{_MODULE_NAME}:factory")
    monkeypatch.setattr(registry_module, "entry_points", lambda *, group: [point])
    return BackendRegistry()


def test_explicit_registration_of_example_factory_needs_no_source_edits(
    example_on_path: None,
) -> None:
    module = importlib.import_module(_MODULE_NAME)
    registry = BackendRegistry()
    registry.register("example-echo", module.factory)
    assert registry.get("example-echo").capabilities.supported_formats == ["text"]


@pytest.fixture
def example_on_path() -> Iterator[None]:
    """Make the example distribution importable, then clean up."""
    path_entry = str(_EXAMPLE_SRC)
    sys.path.insert(0, path_entry)
    try:
        yield
    finally:
        sys.path.remove(path_entry)
        for name in [n for n in sys.modules if n.startswith(_MODULE_NAME)]:
            del sys.modules[name]


def test_example_pyproject_declares_the_frozen_entry_point_group() -> None:
    raw = tomllib.loads((_EXAMPLE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    group = raw["project"]["entry-points"][registry_module.ENTRY_POINT_GROUP]
    assert group == {"example-echo": f"{_MODULE_NAME}:factory"}


def test_example_pyproject_is_a_standalone_distribution() -> None:
    raw = tomllib.loads((_EXAMPLE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert raw["project"]["name"] == "parsecraft-example-backend"
    assert "parsecraft" in raw["project"]["dependencies"]
