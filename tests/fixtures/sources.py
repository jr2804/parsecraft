"""Manifest loader for public test documents (provenance, license, acquisition).

``sources.toml`` next to this module records where permissively licensed real
documents come from, why they are suitable, and how to obtain them locally.
Nothing is downloaded at import time: fetching happens only in the opt-in
corpus tier (``pytest --run-corpus`` / ``mise run test-corpus``), so
``mise test`` always stays offline.

Phase 4 auto-mode seam: ``difficulty``, ``features``, ``pages`` and
``approx_size`` are the per-document expectations the auto-mode (Jev) routing
harness will assert against — they live here as DATA on purpose. When the
harness lands (a future ``tests/test_auto_mode.py``), extend this manifest
with routing expectations; do not encode routing rules inside tests.
"""

from __future__ import annotations

import tomllib
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tests.fixtures.documents import FORMATS

MANIFEST_PATH = Path(__file__).with_name("sources.toml")

_DOWNLOADS_DIR = "tests/downloads"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class Difficulty(StrEnum):
    """Corpus complexity tier for one document."""

    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


class Feature(StrEnum):
    """Content feature a document is expected to exercise during extraction."""

    EQUATIONS = "equations"
    FIGURES = "figures"
    CODE = "code"
    IMAGES = "images"
    TABLES = "tables"


class DocumentSource(BaseModel):
    """Provenance, license, corpus expectations, and acquisition recipe."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    format: str
    media_type: str = Field(min_length=1)
    url: str = Field(pattern=r"^https://")
    license: str = Field(min_length=1)
    license_url: str = Field(pattern=r"^https://")
    why: str = Field(min_length=1)
    filename: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    download_step: str = Field(min_length=1)
    difficulty: Difficulty
    approx_size: int | None = Field(default=None, ge=0)
    features: tuple[Feature, ...] = ()
    pages: int | None = Field(default=None, ge=1)
    sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    manual: bool = False
    mutable: bool = False

    @property
    def cache_name(self) -> str:
        """Cache file name: ``sha256-filename`` when pinned, ``filename`` when mutable."""
        if self.mutable:
            return self.filename
        if self.sha256 is None:
            msg = f"{self.id!r} has no pinned sha256 — nothing to key the cache on"
            raise ValueError(msg)
        return f"{self.sha256}-{self.filename}"

    @model_validator(mode="after")
    def _format_is_scaffoldable(self) -> DocumentSource:
        if self.format not in FORMATS:
            msg = f"format {self.format!r} is not scaffoldable; covers: {', '.join(FORMATS)}"
            raise ValueError(msg)
        if "\n" in self.download_step:
            msg = "download_step must be a single line"
            raise ValueError(msg)
        if _DOWNLOADS_DIR not in self.download_step:
            msg = f"download_step must write into {_DOWNLOADS_DIR}"
            raise ValueError(msg)
        if self.mutable and self.sha256 is not None:
            msg = f"{self.id!r} is mutable; do not pin sha256 (upstream content changes)"
            raise ValueError(msg)
        return self


class SourcesManifest(BaseModel):
    """Parsed ``sources.toml``."""

    sources: tuple[DocumentSource, ...]

    @property
    def downloadable(self) -> tuple[DocumentSource, ...]:
        """Sources the corpus tier may fetch; manual acquisitions excluded."""
        return tuple(source for source in self.sources if not source.manual)

    @model_validator(mode="after")
    def _unique_ids(self) -> SourcesManifest:
        ids = [source.id for source in self.sources]
        duplicates = sorted({source_id for source_id in ids if ids.count(source_id) > 1})
        if duplicates:
            msg = f"duplicate source ids: {duplicates}"
            raise ValueError(msg)
        return self


def load_sources(path: Path = MANIFEST_PATH) -> SourcesManifest:
    """Load and validate the public-document manifest."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return SourcesManifest.model_validate(raw)
