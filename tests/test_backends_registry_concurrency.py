"""Registry thread-safety: single-flight discovery, atomic state, no lock across loads.

All tests are offline and deterministic: threads synchronize on
``threading.Barrier``/``Event`` (never sleeps), stub factories never touch
the network, and every assertion is post-hoc on collected state.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest

from parsecraft.backends import registry as registry_module
from parsecraft.backends.errors import BackendAlreadyRegisteredError, BackendNotFoundError
from parsecraft.backends.protocol import (
    AnalysisResult,
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    BackendFactory,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
    SourceDocument,
)
from parsecraft.backends.registry import BackendRegistry

_WORKERS = 16
_ITERATIONS = 40


class _StubBackend:
    """Minimal caller-owned instance returned by the stub factories."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.capabilities = BackendCapabilities(supported_formats=["text/plain"])

    @staticmethod
    def analyze(source: SourceDocument) -> AnalysisResult:
        msg = "registry concurrency tests never convert"
        raise AssertionError(msg)

    @staticmethod
    def convert(request: ConversionRequest) -> BackendResult:
        msg = "registry concurrency tests never convert"
        raise AssertionError(msg)


class _StubFactory:
    def __init__(self, name: str) -> None:
        self.descriptor = BackendDescriptor(
            name=name,
            capabilities=BackendCapabilities(supported_formats=["text/plain"]),
        )
        self.calls = 0

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        self.calls += 1
        return _StubBackend(self.descriptor.name)


class _FakeEntryPoint:
    def __init__(self, name: str, factory: BackendFactory | Exception) -> None:
        self.name = name
        self._factory = factory

    def load(self) -> BackendFactory:
        if isinstance(self._factory, Exception):
            raise self._factory
        return self._factory


class _EntryPointsSpy:
    """Fake ``importlib.metadata.entry_points`` counting how often a scan runs."""

    def __init__(self, points: list[_FakeEntryPoint]) -> None:
        self.points = points
        self.scans = 0

    def __call__(self, *, group: str) -> list[_FakeEntryPoint]:
        assert group == registry_module.ENTRY_POINT_GROUP
        self.scans += 1
        return list(self.points)


@pytest.fixture(autouse=True)
def _no_real_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate from installed entry points; discovery tests re-patch with a spy."""
    monkeypatch.setattr(registry_module, "entry_points", lambda *, group: [])


# ── Discovery ────────────────────────────────────────────────────────────────────


def test_concurrent_first_use_scans_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    points = [
        _FakeEntryPoint("alpha", _concrete_factory("alpha")),
        _FakeEntryPoint("broken", RuntimeError("plugin exploded")),
        _FakeEntryPoint("beta", _concrete_factory("beta")),
        _FakeEntryPoint("bad2", ValueError("bad descriptor")),
    ]
    spy = _EntryPointsSpy(points)
    monkeypatch.setattr(registry_module, "entry_points", spy)

    # Sequential reference (manual registration — no discovery, no spy use):
    reference = BackendRegistry()
    reference._entry_points_loaded = True  # isolate: the spy counts only the concurrent registry
    reference.register("alpha", _concrete_factory("alpha"))
    reference.register("beta", _concrete_factory("beta"))
    expected_fingerprint = reference.fingerprint()

    registry = BackendRegistry()

    def _work(_index: int) -> tuple[str, list[str], tuple[str, ...]]:
        return (
            registry.fingerprint(),
            [d.name for d in registry.list_backends()],
            tuple(sorted(registry.load_errors)),
        )

    results = _run_threads(_WORKERS, _work)

    assert spy.scans == 1  # single-flight: one metadata scan for all threads
    for fingerprint, names, load_errors in results:
        assert fingerprint == expected_fingerprint
        assert names == ["alpha", "beta"]
        assert load_errors == ("bad2", "broken")  # no spurious AlreadyRegistered pollution


def test_concurrent_registration_has_exactly_one_winner_per_name() -> None:
    registry = BackendRegistry()
    name = "contended"

    def _work(index: int) -> object:
        try:
            registry.register(name, _concrete_factory(name))
            return "registered"
        except BackendAlreadyRegisteredError:
            return "duplicate"

    outcomes = _run_threads(8, _work)
    assert outcomes.count("registered") == 1
    assert outcomes.count("duplicate") == 7
    assert [d.name for d in registry.list_backends()] == [name]


def test_concurrent_distinct_registrations_all_land_sorted() -> None:
    registry = BackendRegistry()
    names = [f"backend-{i:03d}" for i in range(_WORKERS * 4)]

    def _work(index: int) -> object:
        my_name = names[index]
        registry.register(my_name, _concrete_factory(my_name))
        # Interleave reads with writes from the same thread:
        descriptors = registry.list_backends()
        assert [d.name for d in descriptors] == sorted(d.name for d in descriptors)
        return registry.load_errors

    _run_threads(_WORKERS, lambda i: [_work(i * 4 + offset) for offset in range(4)])

    assert [d.name for d in registry.list_backends()] == names  # every name landed
    assert registry.load_errors == {}
    assert registry.fingerprint() == _sequential_fingerprint(names)


def _sequential_fingerprint(names: list[str]) -> str:
    """Reference fingerprint computed on a fresh registry, single-threaded."""
    reference = BackendRegistry()
    for name in names:
        reference.register(name, _concrete_factory(name))
    return reference.fingerprint()


# ── The lock must not cover heavy work ───────────────────────────────────────────


def test_readers_are_not_blocked_while_a_factory_is_loading() -> None:
    """A model load (factory) must never hold up concurrent lookups."""
    loading_started = threading.Event()
    release_loading = threading.Event()
    reader_finished = threading.Event()

    class _SlowFactory:
        descriptor = BackendDescriptor(
            name="slow",
            capabilities=BackendCapabilities(supported_formats=["text/plain"]),
        )

        def __call__(self, config: BackendConfig) -> DocumentBackend:
            loading_started.set()
            assert release_loading.wait(timeout=10), "test never released the load"
            return _StubBackend("slow")

    registry = BackendRegistry()
    registry.register("slow", _SlowFactory())

    def _load() -> None:
        registry.create("slow")

    def _read() -> None:
        assert [d.name for d in registry.list_backends()] == ["slow"]
        _ = registry.fingerprint()
        _ = registry.load_errors
        reader_finished.set()

    loader = threading.Thread(target=_load)
    loader.start()
    assert loading_started.wait(timeout=10), "factory never started"

    reader = threading.Thread(target=_read)
    reader.start()
    # If the registry lock were held across factory(), this wait would time
    # out and fail while the loader is still mid-load.
    assert reader_finished.wait(timeout=5), "lookups stalled behind a model load"
    reader.join(timeout=5)

    release_loading.set()
    loader.join(timeout=10)
    assert not loader.is_alive()


# ── Instantiation semantics: fresh instance per call, race-free ──────────────────


def test_concurrent_creates_yield_one_instance_per_call() -> None:
    registry = BackendRegistry()
    factory = _concrete_factory("shared")
    registry.register("shared", factory)

    def _work(index: int) -> object:
        return registry.create("shared")

    instances = _run_threads(_WORKERS, _work)

    # Caller-owned residency contract: no memoization, no shared instance,
    # and no lost/duplicated factory calls:
    assert len(instances) == _WORKERS
    assert len({id(instance) for instance in instances}) == _WORKERS
    assert registry.get("shared").name == "shared"


def test_create_missing_backend_is_not_found_under_concurrency() -> None:
    registry = BackendRegistry()
    registry.register("present", _concrete_factory("present"))

    def _work(index: int) -> object:
        try:
            registry.create("absent")
        except BackendNotFoundError:
            return "not-found"
        return "unexpected"

    outcomes = _run_threads(_WORKERS, _work)
    assert outcomes == ["not-found"] * _WORKERS


def _concrete_factory(name: str) -> BackendFactory:
    """A structurally conforming factory (registry.register's isinstance check)."""
    factory = _StubFactory(name)

    class _Bound:
        descriptor = factory.descriptor

        def __call__(self, config: BackendConfig) -> DocumentBackend:
            return factory(config)

    return _Bound()


def _run_threads[T](count: int, work: Callable[[int], T]) -> list[T]:
    """Start ``count`` threads on one barrier; collect results/exceptions."""
    barrier = threading.Barrier(count)
    results: list[T] = []
    errors: list[BaseException] = []

    def _target(index: int) -> None:
        try:
            barrier.wait(timeout=10)
            results.append(work(index))
        except BaseException as exc:  # noqa: BLE001 - surfaced via assertion below
            errors.append(exc)

    threads = [threading.Thread(target=_target, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "thread failed to finish (possible deadlock)"
    assert errors == [], f"worker exceptions: {errors!r}"
    return results
