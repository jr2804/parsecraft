"""Backend protocol, registry precedence, lazy entry-point discovery."""

from __future__ import annotations

from collections.abc import Callable, Iterable

import pytest
from pydantic import ValidationError

from parsecraft.backends import (
    ENTRY_POINT_GROUP,
    AnalysisResult,
    BackendAlreadyRegisteredError,
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    BackendError,
    BackendFactory,
    BackendLoadError,
    BackendNotFoundError,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
    SourceDocument,
    default_registry,
)
from parsecraft.backends import DependencyUnavailableError as public_error
from parsecraft.backends import registry as registry_module
from parsecraft.backends.errors import DependencyUnavailableError
from parsecraft.backends.registry import BackendRegistry
from parsecraft.ir.models import PageRange

# ── Test doubles ─────────────────────────────────────────────────────────


class _Backend:
    name = "fake"
    capabilities = BackendCapabilities(supported_formats=["txt"])

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        msg = "not under test"
        raise AssertionError(msg)

    @staticmethod
    def convert(request: ConversionRequest) -> BackendResult:
        msg = "not under test"
        raise AssertionError(msg)


class FakeFactory:
    """Light factory implementing the public BackendFactory contract."""

    descriptor = BackendDescriptor(
        name="fake",
        capabilities=BackendCapabilities(supported_formats=["txt"], requires_gpu=False),
    )

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        return _Backend()


class GpuFactory:
    descriptor = BackendDescriptor(
        name="gpu-one",
        capabilities=BackendCapabilities(
            supported_formats=["pdf"],
            requires_gpu=True,
            estimated_vram_gb=4.5,
            optional_dependency_group="ocr-ovis",
        ),
    )

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        return _Backend()


class MismatchedFactory:
    descriptor = BackendDescriptor(name="different-name", capabilities=BackendCapabilities())

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        return _Backend()


class FakeEntryPoint:
    def __init__(self, name: str, loader: Callable[[], object]) -> None:
        self.name = name
        self._loader = loader
        self.loads = 0

    def load(self) -> object:
        self.loads += 1
        return self._loader()


# ── Explicit registration ────────────────────────────────────────────────


def test_explicit_register_get_create() -> None:
    registry = BackendRegistry()
    registry.register("fake", FakeFactory())
    descriptor = registry.get("fake")
    assert descriptor.name == "fake"
    assert descriptor.capabilities.supported_formats == ["txt"]
    backend = registry.create("fake", BackendConfig(name="fake", options={"x": 1}))
    assert backend.name == "fake"


def test_list_backends_is_sorted_and_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(monkeypatch, [])
    registry = BackendRegistry()
    registry.register("gpu-one", GpuFactory())
    registry.register("fake", FakeFactory())
    assert [d.name for d in registry.list_backends()] == ["fake", "gpu-one"]


def test_supported_formats_unions_registered_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(monkeypatch, [])
    registry = BackendRegistry()
    registry.register("gpu-one", GpuFactory())
    registry.register("fake", FakeFactory())
    assert registry.supported_formats() == ["pdf", "txt"]


def test_supported_formats_empty_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(monkeypatch, [])
    assert BackendRegistry().supported_formats() == []


def test_fingerprint_is_deterministic_and_set_sensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_points(monkeypatch, [])
    base = BackendRegistry()
    base.register("fake", FakeFactory())
    same = BackendRegistry()
    same.register("fake", FakeFactory())
    assert base.fingerprint() == same.fingerprint()
    base.register("gpu-one", GpuFactory())
    assert base.fingerprint() != same.fingerprint()


def test_descriptor_version_defaults() -> None:
    descriptor = BackendDescriptor(name="x", capabilities=BackendCapabilities())
    assert descriptor.version == "0.0.0"


def test_duplicate_explicit_registration_is_rejected() -> None:
    registry = BackendRegistry()
    registry.register("fake", FakeFactory())
    with pytest.raises(BackendAlreadyRegisteredError) as excinfo:
        registry.register("fake", FakeFactory())
    assert excinfo.value.name == "fake"
    assert isinstance(excinfo.value, BackendError)


def test_descriptor_name_must_match_registration_name() -> None:
    registry = BackendRegistry()
    with pytest.raises(ValueError, match="does not match registration name"):
        registry.register("fake", MismatchedFactory())


def test_non_factory_registration_is_rejected() -> None:
    registry = BackendRegistry()
    with pytest.raises(TypeError, match="does not implement BackendFactory"):
        registry.register("fake", object())  # ty: ignore[invalid-argument-type]


def test_factory_protocol_is_runtime_checkable() -> None:
    assert isinstance(FakeFactory(), BackendFactory)
    assert not isinstance(object(), BackendFactory)


def test_unknown_backend_raises_not_found() -> None:
    registry = BackendRegistry()
    with pytest.raises(BackendNotFoundError) as get_info:
        registry.get("nope")
    assert get_info.value.name == "nope"
    with pytest.raises(BackendNotFoundError) as create_info:
        registry.create("nope")
    assert create_info.value.name == "nope"


def test_create_defaults_to_named_config() -> None:
    registry = BackendRegistry()
    registry.register("fake", FakeFactory())
    # _Backend never reads config; reaching instantiation without error is the contract.
    assert registry.create("fake") is not None


# ── Lazy entry-point discovery ───────────────────────────────────────────


def test_entry_points_discovered_lazily_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = BackendRegistry()
    point = FakeEntryPoint("fake", FakeFactory)
    groups = _patch_entry_points(monkeypatch, [point])
    assert groups == []  # nothing scanned before first use
    registry.list_backends()
    registry.list_backends()
    registry.get("fake")
    registry.create("fake")
    assert groups == [ENTRY_POINT_GROUP]
    assert point.loads == 1


def test_entry_point_group_is_frozen() -> None:
    assert ENTRY_POINT_GROUP == "parsecraft.backends"


def test_explicit_registration_wins_over_entry_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = BackendRegistry()

    class ExplicitOne(FakeFactory):
        descriptor = BackendDescriptor(
            name="fake",
            capabilities=BackendCapabilities(supported_formats=["explicit"]),
        )

    registry.register("fake", ExplicitOne())
    point = FakeEntryPoint("fake", FakeFactory)
    _patch_entry_points(monkeypatch, [point])
    descriptors = registry.list_backends()
    assert descriptors[0].capabilities.supported_formats == ["explicit"]
    assert point.loads == 0  # shadowed entry point never even loaded


def test_broken_entry_point_is_recorded_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = BackendRegistry()

    def boom() -> object:
        msg = "plugin exploded"
        raise ImportError(msg)

    point = FakeEntryPoint("broken", boom)
    _patch_entry_points(monkeypatch, [point])
    # Discovery never raises: the failure is data, not an exception path.
    assert registry.list_backends() == []
    errors = registry.load_errors
    assert set(errors) == {"broken"}
    error = errors["broken"]
    assert error.name == "broken"
    assert isinstance(error.cause, ImportError)
    assert "ImportError: plugin exploded" in str(error)
    # Discovery ran exactly once — no rescan, no repeated load attempt.
    registry.list_backends()
    assert point.loads == 1


def test_partially_broken_entry_points_keep_working_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = BackendRegistry()

    def boom() -> object:
        msg = "broken"
        raise RuntimeError(msg)

    good = FakeEntryPoint("fake", FakeFactory)
    bad = FakeEntryPoint("broken", boom)
    _patch_entry_points(monkeypatch, [good, bad])
    assert [d.name for d in registry.list_backends()] == ["fake"]
    assert set(registry.load_errors) == {"broken"}
    assert registry.get("fake").name == "fake"
    assert registry.create("fake") is not None
    assert good.loads == 1
    assert bad.loads == 1


def test_load_errors_copy_is_isolated() -> None:
    registry = BackendRegistry()
    errors = registry.load_errors
    errors["phantom"] = BackendLoadError("phantom")
    assert registry.load_errors == {}


# ── Request / result bounds ──────────────────────────────────────────────


def test_conversion_request_bounds() -> None:
    source = SourceDocument(uri="file:///x.pdf")
    with pytest.raises(ValidationError, match="greater than 0"):
        ConversionRequest(source=source, timeout_s=0)
    with pytest.raises(ValidationError, match="greater than 0"):
        ConversionRequest(source=source, max_output_chars=0)
    request = ConversionRequest(
        source=source,
        page_range=PageRange(start=3, end=5),
        region_ids=frozenset({"r1", "r2"}),
        timeout_s=10.0,
        max_context_tokens=4096,
        max_output_chars=2048,
    )
    assert request.region_ids == frozenset({"r1", "r2"})


def test_source_document_requires_uri() -> None:
    with pytest.raises(ValidationError):
        SourceDocument(uri="")


def test_backend_result_rejects_negative_elapsed() -> None:
    with pytest.raises(ValidationError):
        BackendResult(
            backend={"name": "x", "version": "1"},
            elapsed_s=-1.0,
        )


def test_descriptor_name_pattern_enforced() -> None:
    with pytest.raises(ValidationError, match="pattern"):
        BackendDescriptor(name="Bad Name!", capabilities=BackendCapabilities())


def test_analysis_result_hash_is_pinned_sha256() -> None:
    with pytest.raises(ValidationError, match="pattern"):
        AnalysisResult(source_hash="not-a-hash", page_count=0)


def test_descriptor_serializes_without_factory() -> None:
    descriptor = FakeFactory.descriptor
    dumped = descriptor.model_dump(mode="json")
    assert dumped["name"] == "fake"
    assert set(dumped) == {"name", "version", "capabilities"}


# ── Process-wide registry ──────────────────────────────────────────────────────


def test_default_registry_is_the_shared_singleton() -> None:
    assert isinstance(default_registry, BackendRegistry)
    assert default_registry is registry_module.default_registry


# ── Discovery failure semantics ────────────────────────────────────────────────────


def test_failing_scan_stays_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = BackendRegistry()
    points = [FakeEntryPoint("fake", FakeFactory)]
    calls: list[str] = []

    def flaky_entry_points(*, group: str) -> list[FakeEntryPoint]:
        calls.append(group)
        if len(calls) == 1:
            msg = "scan exploded"
            raise RuntimeError(msg)
        return points

    monkeypatch.setattr(registry_module, "entry_points", flaky_entry_points)
    # A broken *scan* propagates (it is not a plugin failure) and is not cached:
    with pytest.raises(RuntimeError, match="scan exploded"):
        registry.list_backends()
    assert registry.load_errors == {}
    # …so the next use retries, latches, and never scans a third time.
    assert [descriptor.name for descriptor in registry.list_backends()] == ["fake"]
    registry.get("fake")
    registry.create("fake")
    assert len(calls) == 2


def test_entry_point_returning_non_factory_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = BackendRegistry()
    point = FakeEntryPoint("imposter", lambda: "not a factory")
    _patch_entry_points(monkeypatch, [point])
    assert registry.list_backends() == []
    error = registry.load_errors["imposter"]
    assert isinstance(error.cause, TypeError)
    assert "does not implement BackendFactory" in str(error)


def test_recorded_load_error_is_not_raised_on_get_or_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = BackendRegistry()

    def boom() -> object:
        msg = "broken"
        raise ImportError(msg)

    _patch_entry_points(monkeypatch, [FakeEntryPoint("broken", boom)])
    with pytest.raises(BackendNotFoundError):
        registry.get("broken")
    with pytest.raises(BackendNotFoundError):
        registry.create("broken")
    assert set(registry.load_errors) == {"broken"}


def _patch_entry_points(
    monkeypatch: pytest.MonkeyPatch,
    points: Iterable[FakeEntryPoint],
) -> list[str]:
    """Install fake entry points and record every group requested."""
    requested: list[str] = []
    points_list = list(points)

    def fake_entry_points(*, group: str) -> list[FakeEntryPoint]:
        requested.append(group)
        return points_list

    monkeypatch.setattr(registry_module, "entry_points", fake_entry_points)
    return requested


def test_dependency_unavailable_error_is_public_and_typed() -> None:
    """One public signal for 'optional extra missing' (pc-4u7.28)."""
    assert public_error is DependencyUnavailableError  # package re-export, same class
    error = DependencyUnavailableError("some.module", "some-extra")
    assert isinstance(error, BackendError)
    assert (error.module, error.extra) == ("some.module", "some-extra")
    assert str(error) == ("backend dependency 'some.module' is not installed — install the 'some-extra' extra")
