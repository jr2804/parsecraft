"""Backend registry: explicit registration first, lazy entry-point discovery.

Discovery contract:

- ``list_backends()``/``get()``/``create()`` trigger entry-point discovery at
  most once, on first use (``importlib.metadata`` only — never heavy stacks).
- Discovery never raises: a broken plugin is recorded in ``load_errors`` and
  skipped, so one bad plugin cannot brick the registry.
- Entry-point modules must stay light: heavy imports belong inside
  ``factory(config)``. Discovery loads the module; instantiation imports code.
- Explicit ``register()`` always wins over an entry point of the same name.

``ENTRY_POINT_GROUP`` is the frozen public group name (ADR-0001 §1).
"""

from __future__ import annotations

import hashlib
import importlib
import threading
from collections.abc import Mapping
from importlib.metadata import entry_points
from typing import Protocol, cast

from parsecraft.backends.errors import (
    BackendAlreadyRegisteredError,
    BackendLoadError,
    BackendNotFoundError,
)
from parsecraft.backends.protocol import (
    BackendConfig,
    BackendDescriptor,
    BackendFactory,
    DocumentBackend,
)

ENTRY_POINT_GROUP = "parsecraft.backends"

# ponytail (narrowed by pc-4u7.32): registry STATE is lock-guarded and
# discovery is single-flight, but there is no instance residency or GPU
# admission control — `create()` hands out a fresh instance per call, so
# concurrent callers can hold several resident models between them. The
# pipeline executor currently swaps one instance at a time inside the
# 8 GB ceiling; revisit admission control with Phase 7 GPU.


class BackendRegistry:
    """Name → (descriptor, factory) binding with lazy plugin discovery.

    Thread-safety: all registry state (factories, ``load_errors``, the
    discovery flag) is guarded by an internal re-entrant lock and discovery
    runs single-flight. The lock is NEVER held across ``factory(config)`` —
    heavy model loads run outside it, so lookups never stall behind a load.
    Instances are caller-owned: no memoization, no sharing (see AGENTS.md).
    """

    def __init__(self) -> None:
        self._factories: dict[str, BackendFactory] = {}
        self._load_errors: dict[str, BackendLoadError] = {}
        self._entry_points_loaded = False
        self._lock = threading.RLock()

    # ── Registration ─────────────────────────────────────────────────────

    def register(self, name: str, factory: BackendFactory) -> None:
        """Register ``factory`` under ``name`` explicitly (highest precedence)."""
        if not isinstance(factory, BackendFactory):
            msg = f"factory for {name!r} does not implement BackendFactory"
            raise TypeError(msg)
        descriptor = factory.descriptor
        if descriptor.name != name:
            msg = f"descriptor name {descriptor.name!r} does not match registration name {name!r}"
            raise ValueError(msg)
        with self._lock:  # check + insert atomically: one winner per name
            if name in self._factories:
                raise BackendAlreadyRegisteredError(name)
            self._factories[name] = factory

    @property
    def load_errors(self) -> dict[str, BackendLoadError]:
        """Copy of recorded entry-point failures — callers must surface these."""
        with self._lock:
            return dict(self._load_errors)

    # ── Public surface ───────────────────────────────────────────────────

    def list_backends(self) -> list[BackendDescriptor]:
        """All known descriptors, sorted by name (deterministic output)."""
        self._load_entry_points()
        with self._lock:  # snapshot: concurrent register() cannot resize mid-iteration
            return [factory.descriptor for name, factory in sorted(self._factories.items())]

    def get(self, name: str) -> BackendDescriptor:
        """Descriptor for ``name``; raises :class:`BackendNotFoundError`."""
        self._load_entry_points()
        with self._lock:
            factory = self._factories.get(name)
        if factory is None:
            raise BackendNotFoundError(name)
        return factory.descriptor

    def supported_formats(self) -> list[str]:
        """Sorted union of every registered backend's supported formats."""
        formats = {fmt for descriptor in self.list_backends() for fmt in descriptor.capabilities.supported_formats}
        return sorted(formats)

    def suffixes(self, *, installed_only: bool = True) -> set[str]:
        """Source suffixes some registered backend can ingest (a projection).

        A pure PROJECTION of ``parsecraft.pipeline.MEDIA_TYPES`` joined with each
        descriptor's declared ``supported_formats`` — the MIME table stays the
        single source and this is never a second one. Hosts ask "which suffixes
        can my selected backend ingest" through this instead of re-deriving the
        join across two modules.

        ``installed_only=True`` (the default) drops backends whose optional
        extra is absent, so an uninstalled backend never advertises suffixes.
        GPU/VRAM/allow-OCR eligibility stays ``routing.rules.is_hard_eligible``'s
        job (it needs a probed host); a host that wants full eligibility joins
        this projection with that funnel rather than duplicating it here.
        """
        suffixes: set[str] = set()
        for descriptor in self.list_backends():
            suffixes |= self.suffixes_for(descriptor, installed_only=installed_only)
        return suffixes

    def fingerprint(self) -> str:
        """Deterministic identity of the registered backend set — a cache key.

        Derived from each backend's name, version, and supported formats:
        properties of the *installed backend set*, never host hardware. Two
        registries with the same backend set yield the same fingerprint on any
        machine; changing any backend's identity or formats changes it.
        """
        canonical = "\n".join(f"{d.name}@{d.version}|{','.join(sorted(d.capabilities.supported_formats))}" for d in self.list_backends())
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def create(self, name: str, config: BackendConfig | None = None) -> DocumentBackend:
        """Instantiate backend ``name`` — the first heavy-import boundary.

        A fresh, caller-owned instance per call (no memoization): residency
        and VRAM admission belong to the caller, and the registry lock is
        never held across ``factory(config)`` — a multi-minute model load
        never blocks concurrent lookups or registrations.
        """
        factory = self._factory_for(name)
        return factory(config if config is not None else BackendConfig(name=name))

    @staticmethod
    def suffixes_for(descriptor: BackendDescriptor, *, installed_only: bool = True) -> set[str]:
        """The suffixes ONE descriptor can ingest — the per-backend half of :meth:`suffixes`."""
        if installed_only and not _extra_installed(descriptor):
            return set()

        formats = descriptor.capabilities.supported_formats
        return {suffix for suffix, media_type in _media_types().items() if media_type in formats}

    def _factory_for(self, name: str) -> BackendFactory:
        """Locked lookup of ``name``'s factory (discovery first); raises NotFound."""
        self._load_entry_points()
        with self._lock:
            factory = self._factories.get(name)
        if factory is None:
            raise BackendNotFoundError(name)
        return factory

    # ── Discovery ────────────────────────────────────────────────────────

    def _load_entry_points(self) -> None:
        with self._lock:
            if self._entry_points_loaded:
                return
            for entry_point in entry_points(group=ENTRY_POINT_GROUP):
                if entry_point.name in self._factories:
                    continue  # explicit registration wins
                try:
                    # RLock: register() re-enters on this thread; no other
                    # thread interleaves, so a double scan (and the spurious
                    # AlreadyRegistered load_errors it would leave) is
                    # impossible.
                    self.register(entry_point.name, entry_point.load())
                except Exception as exc:  # one broken plugin must not brick discovery
                    self._load_errors[entry_point.name] = BackendLoadError(entry_point.name, exc)
            # Set after the loop: a failing *scan* (vs. a failing plugin)
            # stays retryable; loaded plugins never re-import.
            self._entry_points_loaded = True


#: Process-wide registry used by the CLI and default application wiring.
default_registry = BackendRegistry()


class _AnalysisModule(Protocol):
    """The slice of ``parsecraft.pipeline.analysis`` the projection reads."""

    MEDIA_TYPES: Mapping[str, str]


class _ProbeModule(Protocol):
    """The slice of ``parsecraft.environment.probe`` the projection reads."""

    def extra_present(self, group: str) -> bool:
        """Whether every import package of an extra resolves."""
        ...


def _extra_installed(descriptor: BackendDescriptor) -> bool:
    """Whether a descriptor's optional extra is actually installed (detected)."""
    group = descriptor.capabilities.optional_dependency_group
    if group is None:  # no extra gate — the backend is always a candidate
        return True

    return _extra_present(group)


def _extra_present(group: str) -> bool:
    """``environment.probe.extra_present``, resolved at call time.

    ``parsecraft.environment`` imports this package, so a module-level import here
    would cycle — and an *inline* ``from`` import would be hoisted to the top by
    pyreorder (``[transforms] hoist_inline_imports = true``), reintroducing it.
    ``importlib`` at call time, through a declared Protocol so the attribute
    access stays typed, is the shape that survives both.
    """
    module = cast("_ProbeModule", importlib.import_module("parsecraft.environment.probe"))
    return module.extra_present(group)


def _media_types() -> Mapping[str, str]:
    """The extension→MIME table ``pipeline.MEDIA_TYPES`` — the single source.

    Fetched at call time for the same cycle reason as :func:`_extra_present`:
    ``parsecraft.pipeline`` imports this package.
    """
    module = cast("_AnalysisModule", importlib.import_module("parsecraft.pipeline.analysis"))
    return module.MEDIA_TYPES
