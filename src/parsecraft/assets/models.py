"""Typed records for pinned assets, license acceptance, and cache state."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from parsecraft.backends.protocol import ModelAssetDescriptor

RECORD_VERSION = 1


class AssetPin(BaseModel):
    """A fully pinned asset: descriptor plus per-file integrity data.

    Checksums live here rather than on ``ModelAssetDescriptor`` so the shared
    backend-protocol type stays a pure metadata record; the pin is the
    download/verify contract.
    """

    descriptor: ModelAssetDescriptor
    filenames: list[str] = Field(min_length=1)
    expected_sha256: dict[str, str]


class LicenseAcceptance(BaseModel):
    """Immutable record that a user accepted a model's license terms."""

    record_version: int = RECORD_VERSION
    model_id: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    model_license: str = Field(min_length=1)
    accepted_at: datetime


class CachedAssetFile(BaseModel):
    """One file present in the local asset cache."""

    filename: str
    path: str
    size_bytes: int = Field(ge=0)


class CachedAsset(BaseModel):
    """One pinned model revision present in the local asset cache."""

    model_id: str
    revision: str
    files: list[CachedAssetFile] = Field(default_factory=list)
    total_bytes: int = Field(ge=0)


class CacheReport(BaseModel):
    """Inspection snapshot of the local asset cache."""

    location: str
    assets: list[CachedAsset] = Field(default_factory=list)
    total_bytes: int = Field(ge=0)
