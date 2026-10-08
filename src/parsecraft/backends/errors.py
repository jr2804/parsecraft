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


class DependencyUnavailableError(BackendError):
    """A backend's optional implementation module is not installed."""

    def __init__(self, module: str, extra: str) -> None:
        self.module = module
        self.extra = extra
        super().__init__(f"backend dependency {module!r} is not installed — install the {extra!r} extra")


class UnsupportedDependencyVersionError(BackendError):
    """A dependency is installed but outside the range this build supports."""

    def __init__(self, package: str, actual: str, expected: str) -> None:
        self.package = package
        self.actual = actual
        self.expected = expected
        super().__init__(f"{package}=={actual} does not satisfy the required range {expected!r} — pip install '{package}{expected}'")


class UnsupportedFormatError(BackendError):
    """A backend cannot handle the source's media format.

    Raised (not recorded as a PassFailure) so callers get a typed, inspectable
    signal before any heavy work begins. The pipeline executor catches
    ``BackendError`` uniformly; this subclass names the *why* precisely.
    """

    def __init__(self, media_type: str) -> None:
        self.media_type = media_type
        super().__init__(f"backend does not support format: {media_type!r}")
