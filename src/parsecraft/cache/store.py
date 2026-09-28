"""Content-addressed conversion cache: keyed storage for executed IR documents.

Keys combine source bytes, the registry fingerprint, canonicalized
constraints, and the effective judge identity (built by the pipeline seam);
values are versioned envelopes around a serialized ``DocumentResult``.
Reads are total (any corruption or schema drift is a miss, never an error);
writes are atomic (temp file + ``os.replace``).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from platformdirs import user_cache_path
from pydantic import BaseModel, Field, ValidationError

from parsecraft.ir.models import DocumentResult

#: Envelope schema version — a format change yields a miss, never a mis-read.
CACHE_SCHEMA_VERSION = 2  # v2: DocumentResult.quality carries routing degradation

_CACHE_DIR_ENV = "PARSECRAFT_CACHE_DIR"
_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ENTRY_SUFFIX = ".json"


class CacheEntryInfo(BaseModel):
    """One stored entry, as reported by :meth:`ConversionCache.entries`."""

    key: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class ConversionCache:
    """File-backed conversion cache: content-addressed, atomic, versioned."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else default_cache_root()

    def get(self, key: str) -> DocumentResult | None:
        """Stored document for ``key``, or ``None`` on any miss condition.

        A missing, malformed, truncated, wrong-key, wrong-schema, or
        invalid-payload file is a miss — never an error.
        """
        path = self._path(key)
        if path is None:
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(raw, dict) or raw.get("schema_version") != CACHE_SCHEMA_VERSION or raw.get("key") != key:
            return None
        payload = raw.get("result")
        if not isinstance(payload, dict):
            return None
        try:
            return DocumentResult.model_validate(payload)
        except ValidationError:
            return None

    def put(self, key: str, result: DocumentResult) -> None:
        """Store ``result`` under ``key`` atomically (temp file + replace)."""
        path = self._path(key)
        if path is None:
            raise ValueError(f"invalid cache key: {key!r}")
        envelope = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "key": key,
            "result": result.model_dump(mode="json"),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f".{key}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(envelope, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, path)

    def entries(self) -> list[CacheEntryInfo]:
        """Every stored entry, sorted by key (stable inspect output)."""
        if not self.root.is_dir():
            return []
        found: list[CacheEntryInfo] = []
        for path in sorted(self.root.iterdir()):
            name = path.name
            if not name.endswith(_ENTRY_SUFFIX):
                continue
            key = name[: -len(_ENTRY_SUFFIX)]
            if _KEY_PATTERN.match(key) is None:
                continue
            try:
                if not path.is_file():
                    continue
                size = path.stat().st_size
            except OSError:
                continue
            found.append(CacheEntryInfo(key=key, size_bytes=size))
        return sorted(found, key=lambda entry: entry.key)

    def total_bytes(self) -> int:
        """Sum of stored entry sizes (inspect output)."""
        return sum(entry.size_bytes for entry in self.entries())

    def clear(self) -> int:
        """Delete every stored entry; return the number removed."""
        removed = 0
        for entry in self.entries():
            try:
                (self.root / f"{entry.key}{_ENTRY_SUFFIX}").unlink()
            except OSError:
                continue
            removed += 1
        return removed

    def sweep(self, max_bytes: int) -> int:
        """Delete oldest entries until the cache fits in ``max_bytes``.

        ponytail: oldest-by-mtime sweep only — no LRU, no per-entry TTL, no
        quotas; upgrade path is a policy object (size + age + pinning) once
        conversion entries outgrow a flat directory.
        """
        sized: list[tuple[float, str, Path, int]] = []
        for entry in self.entries():
            path = self.root / f"{entry.key}{_ENTRY_SUFFIX}"
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            sized.append((mtime, entry.key, path, entry.size_bytes))
        sized.sort(key=lambda item: (item[0], item[1]))
        total = sum(item[3] for item in sized)
        removed = 0
        for _, _, path, size in sized:
            if total <= max_bytes:
                break
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
            removed += 1
        return removed

    def _path(self, key: str) -> Path | None:
        if _KEY_PATTERN.match(key) is None:
            return None
        return self.root / f"{key}{_ENTRY_SUFFIX}"


def default_cache_root() -> Path:
    """Conversion-cache root: ``$PARSECRAFT_CACHE_DIR`` or the user cache dir."""
    override = os.environ.get(_CACHE_DIR_ENV, "").strip()
    if override:
        return Path(override)
    return user_cache_path("parsecraft") / "conversions"
