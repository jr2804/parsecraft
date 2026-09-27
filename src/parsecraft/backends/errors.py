"""Backend subsystem exceptions."""

from __future__ import annotations


class BackendError(Exception):
    """Base class for all backend subsystem errors."""


class BackendNotFoundError(BackendError):
    """Raised when a backend name is not registered."""

    def __init__(self, name: str) -> None:
        super().__init__(f"backend not registered: {name!r}")
        self.name = name


class BackendAlreadyRegisteredError(BackendError):
    """Raised when explicit registration collides with an existing name."""

    def __init__(self, name: str) -> None:
        super().__init__(f"backend already registered: {name!r}")
        self.name = name


class BackendLoadError(BackendError):
    """Recorded failure to load an entry-point backend (stored, not raised).

    Discovery never raises — one broken plugin must not brick the registry.
    Instances surface via ``BackendRegistry.load_errors`` so callers can
    report them explicitly.
    """

    def __init__(self, name: str, cause: Exception | None = None) -> None:
        detail = f": {type(cause).__name__}: {cause}" if cause is not None else ""
        super().__init__(f"failed to load backend {name!r} from entry point{detail}")
        self.name = name
        self.cause = cause
