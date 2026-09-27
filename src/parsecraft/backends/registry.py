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
from importlib.metadata import entry_points

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

# ponytail: single-process registry, no locking — revisit with Phase 7 GPU
# admission control if backends get registered concurrently.


class BackendRegistry:
    """Name → (descriptor, factory) binding with lazy plugin discovery."""

    def __init__(self) -> None:
        self._factories: dict[str, BackendFactory] = {}
        self._load_errors: dict[str, BackendLoadError] = {}
        self._entry_points_loaded = False

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
        if name in self._factories:
            raise BackendAlreadyRegisteredError(name)
        self._factories[name] = factory

    @property
    def load_errors(self) -> dict[str, BackendLoadError]:
        """Copy of recorded entry-point failures — callers must surface these."""
        return dict(self._load_errors)

    # ── Public surface ───────────────────────────────────────────────────

    def list_backends(self) -> list[BackendDescriptor]:
        """All known descriptors, sorted by name (deterministic output)."""
        self._load_entry_points()
        return [factory.descriptor for name, factory in sorted(self._factories.items())]

    def get(self, name: str) -> BackendDescriptor:
        """Descriptor for ``name``; raises :class:`BackendNotFoundError`."""
        self._load_entry_points()
        factory = self._factories.get(name)
        if factory is None:
            raise BackendNotFoundError(name)
        return factory.descriptor

    def supported_formats(self) -> list[str]:
        """Sorted union of every registered backend's supported formats."""
        formats = {fmt for descriptor in self.list_backends() for fmt in descriptor.capabilities.supported_formats}
        return sorted(formats)

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
        """Instantiate backend ``name`` — the first heavy-import boundary."""
        self._load_entry_points()
        factory = self._factories.get(name)
        if factory is None:
            raise BackendNotFoundError(name)
        return factory(config if config is not None else BackendConfig(name=name))

    # ── Discovery ────────────────────────────────────────────────────────

    def _load_entry_points(self) -> None:
        if self._entry_points_loaded:
            return
        for entry_point in entry_points(group=ENTRY_POINT_GROUP):
            if entry_point.name in self._factories:
                continue  # explicit registration wins
            try:
                self.register(entry_point.name, entry_point.load())
            except Exception as exc:  # one broken plugin must not brick discovery
                self._load_errors[entry_point.name] = BackendLoadError(entry_point.name, exc)
        # Set after the loop: a failing *scan* (vs. a failing plugin) stays
        # retryable; loaded plugins never re-import.
        self._entry_points_loaded = True


#: Process-wide registry used by the CLI and default application wiring.
default_registry = BackendRegistry()
