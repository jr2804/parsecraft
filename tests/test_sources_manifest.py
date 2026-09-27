"""Public-document manifest: schema, corpus expectations, opt-in downloads.

Offline and fast by default: only manifest validation runs in ``mise test``.
The corpus tier (marker ``corpus`` + ``network``) fetches real documents into
a content-addressed cache keyed by the pinned ``sha256`` and is gated behind
``pytest --run-corpus`` (cold-cache budget: 15 min). The last test in this
file proves both gates hold for the default run.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.fixtures.documents import FORMATS
from tests.fixtures.sources import (
    Difficulty,
    DocumentSource,
    Feature,
    SourcesManifest,
    load_sources,
)

_MANIFEST = load_sources()
_DOWNLOADS_DIR_HINT = "tests/test-cache/downloads"

#: Hard ceiling for a cold-cache corpus refresh (ADR-0001 §6 headline: 15 min).
_CORPUS_BUDGET_S = 900.0
_FETCH_ELAPSED_S: list[float] = []


# ── Manifest schema (offline) ────────────────────────────────────────────────────


def test_manifest_is_non_empty_with_unique_ids() -> None:
    ids = [source.id for source in _MANIFEST.sources]
    assert len(ids) >= 8
    assert len(ids) == len(set(ids))


def test_manifest_covers_every_scaffold_format() -> None:
    assert {source.format for source in _MANIFEST.sources} == set(FORMATS)


def test_corpus_spans_the_full_complexity_range() -> None:
    assert {source.difficulty for source in _MANIFEST.sources} == set(Difficulty)


def test_corpus_covers_every_expected_feature() -> None:
    features = {feature for source in _MANIFEST.sources for feature in source.features}
    assert features == set(Feature)


def test_every_downloadable_source_is_pinned() -> None:
    for source in _MANIFEST.downloadable:
        assert source.sha256 is not None
        assert source.approx_size is not None
        assert source.approx_size > 0
        assert source.cache_name == f"{source.sha256}-{source.filename}"


def test_page_counts_are_recorded_where_known() -> None:
    with_pages = {source.id: source.pages for source in _MANIFEST.sources if source.pages is not None}
    assert with_pages == {"nist-sp-800-53r5": 492, "itu-t-p863": 113}


def test_every_source_declares_https_license_and_reason() -> None:
    for source in _MANIFEST.sources:
        assert source.url.startswith("https://")
        assert source.license_url.startswith("https://")
        assert len(source.license) > 10
        assert len(source.why) > 30
        assert source.filename.endswith(f".{source.format}")


def test_download_steps_are_documented_local_commands() -> None:
    for source in _MANIFEST.downloadable:
        assert source.url in source.download_step
        assert _DOWNLOADS_DIR_HINT in source.download_step


def test_manual_sources_are_excluded_from_automation() -> None:
    manual = [source for source in _MANIFEST.sources if source.manual]
    assert manual, "the ITU-T benchmark must stay a manual acquisition (ADR-0001 §8)"
    automated_ids = {source.id for source in _MANIFEST.downloadable}
    for source in manual:
        assert source.id not in automated_ids
        assert _DOWNLOADS_DIR_HINT in source.download_step


def test_unpinned_source_has_no_cache_key() -> None:
    manual = next(source for source in _MANIFEST.sources if source.manual)
    with pytest.raises(ValueError, match="no pinned sha256"):
        _ = manual.cache_name


def test_unknown_format_is_rejected() -> None:
    with pytest.raises(ValidationError, match="not scaffoldable"):
        DocumentSource(
            id="bad-format",
            format="docx",
            media_type="application/msword",
            url="https://example.com/a.doc",
            license="MIT",
            license_url="https://example.com/license",
            why="documents the rejection path for a non-scaffoldable format",
            filename="a.doc",
            download_step=f"curl -o {_DOWNLOADS_DIR_HINT}/a.doc https://example.com/a.doc",
            difficulty=Difficulty.SIMPLE,
        )


def test_unknown_feature_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DocumentSource(
            id="bad-feature",
            format="txt",
            media_type="text/plain",
            url="https://example.com/a.txt",
            license="MIT",
            license_url="https://example.com/license",
            why="documents the rejection path for an unrecognised corpus feature",
            filename="a.txt",
            download_step=f"curl -o {_DOWNLOADS_DIR_HINT}/a.txt https://example.com/a.txt",
            difficulty=Difficulty.SIMPLE,
            features=["charts"],
        )


def test_bad_sha256_is_rejected() -> None:
    with pytest.raises(ValidationError, match="pattern"):
        DocumentSource(
            id="bad-hash",
            format="txt",
            media_type="text/plain",
            url="https://example.com/a.txt",
            license="MIT",
            license_url="https://example.com/license",
            why="documents the rejection path for a malformed content hash",
            filename="a.txt",
            download_step=f"curl -o {_DOWNLOADS_DIR_HINT}/a.txt https://example.com/a.txt",
            difficulty=Difficulty.SIMPLE,
            sha256="not-a-hash",
        )


def test_download_step_must_target_the_cache_dir() -> None:
    with pytest.raises(ValidationError, match="must write into"):
        DocumentSource(
            id="stray-target",
            format="txt",
            media_type="text/plain",
            url="https://example.com/a.txt",
            license="MIT",
            license_url="https://example.com/license",
            why="documents the rejection path for a download step outside the cache",
            filename="a.txt",
            download_step="curl -o /tmp/a.txt https://example.com/a.txt",
            difficulty=Difficulty.SIMPLE,
        )


def test_duplicate_source_ids_are_rejected() -> None:
    source = _MANIFEST.sources[0]
    with pytest.raises(ValidationError, match="duplicate source ids"):
        SourcesManifest(sources=[source, source])


# ── Corpus tier (opt-in: --run-corpus) ────────────────────────────────────────────


@pytest.mark.corpus
@pytest.mark.network
@pytest.mark.parametrize("source_id", [source.id for source in _MANIFEST.downloadable])
def test_corpus_document_is_cached_and_matches_pinned_hash(source_id: str, downloads_dir: Path) -> None:
    source = _by_id(source_id)
    assert source.approx_size is not None
    cache_path = downloads_dir / source.cache_name
    started = time.monotonic()
    data = _acquire(source, cache_path)
    _FETCH_ELAPSED_S.append(time.monotonic() - started)
    assert cache_path.is_file()
    assert hashlib.sha256(data).hexdigest() == source.sha256
    assert len(data) == source.approx_size
    _assert_plausible(source, data)


def _by_id(source_id: str) -> DocumentSource:
    """Resolve one manifest entry from its id."""
    for source in _MANIFEST.sources:
        if source.id == source_id:
            return source
    msg = f"unknown source id: {source_id}"
    raise KeyError(msg)


def _acquire(source: DocumentSource, cache_path: Path) -> bytes:
    """Content-addressed acquisition: reuse on hash match, else fetch, verify, store."""
    expected = source.sha256
    assert expected is not None, "downloadable corpus entries must be content-pinned"
    if cache_path.is_file():
        cached = cache_path.read_bytes()
        if hashlib.sha256(cached).hexdigest() == expected:
            return cached  # hash match — never re-download
    data = _fetch(source.url)
    digest = hashlib.sha256(data).hexdigest()
    assert digest == expected, f"corpus drift for {source.id}: pinned {expected}, got {digest} — re-pin sources.toml deliberately"
    cache_path.write_bytes(data)
    return data


def _fetch(url: str) -> bytes:
    """GET ``url`` with a declared user agent (only ever called from opted-in tests)."""
    request = urllib.request.Request(url, headers={"User-Agent": "parsecraft-tests/0.1"})  # noqa: S310 — https URLs from the curated manifest
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 — https URLs from the curated manifest
        return response.read()


def _assert_plausible(source: DocumentSource, data: bytes) -> None:
    """Sniff the payload so a landing page or error page cannot pass as content."""
    if source.format == "pdf":
        assert data.startswith(b"%PDF-")
    elif source.format == "json":
        json.loads(data.decode("utf-8"))
    elif source.format == "csv":
        rows = list(csv.reader(io.StringIO(data.decode("utf-8"))))
        assert len(rows) > 1
    elif source.format == "html":
        assert b"<html" in data.lower()
    else:
        assert data.decode("utf-8").strip()


@pytest.mark.corpus
def test_corpus_cold_cache_refresh_stays_within_budget() -> None:
    """Wall time spent fetching this session must stay under the 15 min ceiling."""
    total = sum(_FETCH_ELAPSED_S)
    assert total < _CORPUS_BUDGET_S, f"corpus fetch took {total:.1f}s of {_CORPUS_BUDGET_S:.0f}s budget"


def test_gated_tiers_are_skipped_without_their_opt_in_flags() -> None:
    """The default run must stay offline and fast: corpus/network skip by default."""
    proc = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_sources_manifest.py",
            "-m",
            "corpus or network",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "skipped" in proc.stdout
    assert "passed" not in proc.stdout
