"""Typed errors for the asset manager."""

from __future__ import annotations


class AssetError(Exception):
    """Base class for all asset-manager failures."""

    def __init__(self, model_id: str = "", revision: str = "", detail: str = "") -> None:
        self.model_id = model_id
        self.revision = revision
        self.detail = detail
        super().__init__(f"{model_id}@{revision}: {detail}")


class LicenseNotAcceptedError(AssetError):
    """The asset requires explicit license acceptance and none is recorded."""


class OfflineModeError(AssetError):
    """A required asset is missing locally and the manager is in offline mode."""


class ChecksumMismatchError(AssetError):
    """A downloaded file does not match its pinned SHA-256 checksum."""

    def __init__(self, model_id: str, revision: str, filename: str, expected: str, actual: str) -> None:
        self.filename = filename
        self.expected_sha256 = expected
        self.actual_sha256 = actual
        super().__init__(model_id, revision, f"checksum mismatch for {filename!r}: expected {expected}, got {actual}")


class InsufficientDiskSpaceError(AssetError):
    """The cache volume does not have enough free space for the download."""

    def __init__(self, model_id: str, revision: str, required_bytes: int, available_bytes: int) -> None:
        self.required_bytes = required_bytes
        self.available_bytes = available_bytes
        super().__init__(
            model_id,
            revision,
            f"insufficient disk space: need {required_bytes} bytes, {available_bytes} available",
        )


class DownloaderUnavailableError(AssetError):
    """The optional download dependency (e.g. ``huggingface-hub``) is not installed."""
