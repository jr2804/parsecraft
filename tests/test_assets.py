"""Offline tests for the asset manager: fake downloader, no network."""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import subprocess
import sys
import textwrap
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import parsecraft.assets.manager as manager_module
from parsecraft.assets import (
    AssetError,
    AssetManager,
    AssetPin,
    ChecksumMismatchError,
    DownloaderUnavailableError,
    GitHubDownloader,
    InsufficientDiskSpaceError,
    LicenseNotAcceptedError,
    OfflineModeError,
    default_downloader,
    downloader_for,
    sha256_of,
    slug,
)
from parsecraft.backends.protocol import ModelAssetDescriptor, ModelSource

CONTENT = b"weights-bytes-for-tests"
DIGEST = hashlib.sha256(CONTENT).hexdigest()


class FakeDownloader:
    """Writes pinned file content into dest_dir (stands in for hf_hub_download)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    def download(self, model_id: str, revision: str, filename: str, dest_dir: str) -> str:
        self.calls.append((model_id, revision, filename, dest_dir))
        target = Path(dest_dir) / filename
        target.write_bytes(CONTENT)
        return str(target)


# ── GitHub adapter + source discriminator (ADR-0008 decision 11) ───────────


class _FakeResponse:
    """Minimal context-manager response, as ``urlopen`` returns."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


# ── helpers ────────────────────────────────────────────────────────────────


def test_slug_replaces_slash() -> None:
    assert slug("acme/model") == "acme--model"
    assert slug("plain") == "plain"


def test_sha256_of_matches_known_digest(tmp_path: Path) -> None:
    big = tmp_path / "big.bin"
    big.write_bytes(CONTENT * 3)
    assert sha256_of(big) == hashlib.sha256(CONTENT * 3).hexdigest()


# ── construction and cache basics ──────────────────────────────────────────


def test_default_cache_dir_uses_platformdirs_without_creating_it() -> None:
    manager = AssetManager(downloader=FakeDownloader())
    assert manager.cache_location().endswith("models")
    assert Path(manager.cache_location()).name == "models"


def test_package_version_prefers_metadata_with_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    monkeypatch.setattr("parsecraft.assets.manager._dist_version", lambda _name: "9.9.9")
    assert manager.package_version() == "9.9.9"
    monkeypatch.setattr(
        "parsecraft.assets.manager._dist_version",
        lambda _name: (_ for _ in ()).throw(PackageNotFoundError("parsecraft")),
    )
    assert manager.package_version() == "0.0.0"


def test_available_disk_bytes_is_positive(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    assert AssetManager(cache_dir=tmp_path, downloader=FakeDownloader()).available_disk_bytes() > 0


# ── cache inspection / cleanup ─────────────────────────────────────────────


def test_inspect_cache_empty_and_missing_dir(tmp_path: Path) -> None:
    assert AssetManager(cache_dir=tmp_path / "missing", downloader=FakeDownloader()).inspect_cache().total_bytes == 0


def test_inspect_cache_lists_direct_children_and_counts_nested_bytes(tmp_path: Path) -> None:
    revision_dir = tmp_path / "acme--model" / "abc123"
    revision_dir.mkdir(parents=True)
    (revision_dir / "weights.bin").write_bytes(CONTENT)
    nested = revision_dir / "nested"
    nested.mkdir()
    (nested / "deeper.bin").write_bytes(b"nested-weights")
    report = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader()).inspect_cache()
    assert len(report.assets) == 1
    asset = report.assets[0]
    assert asset.model_id == "acme/model"
    assert asset.revision == "abc123"
    assert [f.filename for f in asset.files] == ["weights.bin"]
    assert asset.total_bytes == len(CONTENT) + len(b"nested-weights")
    assert report.total_bytes == len(CONTENT) + len(b"nested-weights")


def test_remove_missing_returns_false(tmp_path: Path) -> None:
    assert AssetManager(cache_dir=tmp_path, downloader=FakeDownloader()).remove("acme/model", "abc123") is False


def test_remove_deletes_revision_and_empty_model_dir(tmp_path: Path) -> None:
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    revision_dir = tmp_path / "acme--model" / "abc123"
    revision_dir.mkdir(parents=True)
    (revision_dir / "weights.bin").write_bytes(CONTENT)
    assert manager.remove("acme/model", "abc123") is True
    assert not (tmp_path / "acme--model").exists()


def test_remove_keeps_nonempty_model_dir(tmp_path: Path) -> None:
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    (tmp_path / "acme--model" / "abc123").mkdir(parents=True)
    (tmp_path / "acme--model" / "def456").mkdir(parents=True)
    assert manager.remove("acme/model", "abc123") is True
    assert (tmp_path / "acme--model" / "def456").is_dir()


def test_clear_cache_counts_revisions_and_ignores_missing(tmp_path: Path) -> None:
    manager = AssetManager(cache_dir=tmp_path / "missing", downloader=FakeDownloader())
    assert manager.clear_cache() == 0
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    (tmp_path / "acme--model").mkdir()
    (tmp_path / "other--model" / "r1").mkdir(parents=True)
    assert manager.clear_cache() == 2
    assert list(tmp_path.iterdir()) == []


# ── license acceptance records ─────────────────────────────────────────────


def test_license_acceptance_none_when_unrecorded(tmp_path: Path) -> None:
    assert AssetManager(cache_dir=tmp_path, downloader=FakeDownloader()).license_acceptance("acme/model", "abc123") is None


def test_accept_license_persists_and_round_trips(tmp_path: Path) -> None:
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    descriptor = make_descriptor()
    acceptance = manager.accept_license(descriptor)
    assert acceptance.accepted_at.tzinfo is not None
    stored = manager.license_acceptance("acme/model", "abc123")
    assert stored is not None
    assert stored.model_license == "apache-2.0"
    assert stored.record_version == 1
    raw = json.loads((tmp_path / "license-acceptances.json").read_text(encoding="utf-8"))
    assert set(raw) == {"acme/model@abc123"}


def test_accept_license_appends_to_existing_store(tmp_path: Path) -> None:
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    manager.accept_license(make_descriptor(model_revision="r1"))
    manager.accept_license(make_descriptor(model_revision="r2"))
    assert manager.license_acceptance("acme/model", "r2") is not None
    assert manager.license_acceptance("acme/model", "r1") is not None


# ── local model directories ────────────────────────────────────────────────


def test_local_model_dir_rejects_missing(tmp_path: Path) -> None:
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    with pytest.raises(AssetError, match="local model dir not found"):
        manager.local_model_dir(make_descriptor(), tmp_path / "nope")


def test_local_model_dir_accepts_existing(tmp_path: Path) -> None:
    target = tmp_path / "model-dir"
    target.mkdir()
    assert AssetManager(cache_dir=tmp_path, downloader=FakeDownloader()).local_model_dir(make_descriptor(), target) == target


# ── ensure: offline, license, disk space, checksums ────────────────────────


def test_has_all_files(tmp_path: Path) -> None:
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    assert manager.has_all_files(make_pin()) is False
    target = tmp_path / "acme--model" / "abc123"
    target.mkdir(parents=True)
    (target / "weights.bin").write_bytes(CONTENT)
    assert manager.has_all_files(make_pin()) is True


def test_ensure_offline_missing_raises(tmp_path: Path) -> None:
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader(), offline=True)
    with pytest.raises(OfflineModeError):
        manager.ensure(make_pin())


def test_ensure_offline_with_cached_files_skips_download(tmp_path: Path) -> None:
    downloader = FakeDownloader()
    manager = AssetManager(cache_dir=tmp_path, downloader=downloader, offline=True)
    target = tmp_path / "acme--model" / "abc123"
    target.mkdir(parents=True)
    (target / "weights.bin").write_bytes(CONTENT)
    assert manager.ensure(make_pin()) == [str(target / "weights.bin")]
    assert downloader.calls == []


def test_ensure_requires_license_acceptance(tmp_path: Path) -> None:
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    with pytest.raises(LicenseNotAcceptedError, match="requires explicit acceptance"):
        manager.ensure(make_pin(descriptor=make_descriptor(requires_user_acceptance=True)))


def test_ensure_with_recorded_acceptance_downloads(tmp_path: Path) -> None:
    downloader = FakeDownloader()
    manager = AssetManager(cache_dir=tmp_path, downloader=downloader)
    descriptor = make_descriptor(requires_user_acceptance=True)
    manager.accept_license(descriptor)
    paths = manager.ensure(make_pin(descriptor=descriptor))
    assert paths == [str(tmp_path / "acme--model" / "abc123" / "weights.bin")]
    assert downloader.calls == [("acme/model", "abc123", "weights.bin", str(tmp_path / "acme--model" / "abc123"))]
    assert Path(paths[0]).read_bytes() == CONTENT


def test_ensure_skips_disk_check_without_declared_size(tmp_path: Path) -> None:
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader(), min_free_bytes=2**60)
    assert manager.ensure(make_pin())  # size_bytes is None -> check bypassed


def test_ensure_insufficient_disk_space(tmp_path: Path) -> None:
    manager = AssetManager(
        cache_dir=tmp_path,
        downloader=FakeDownloader(),
        min_free_bytes=2**60,
    )
    pin = make_pin(descriptor=make_descriptor(size_bytes=1))
    with pytest.raises(InsufficientDiskSpaceError):
        manager.ensure(pin)


def test_ensure_disk_space_sufficient_when_free_exceeds_headroom(tmp_path: Path) -> None:
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader(), min_free_bytes=0)
    manager.ensure(make_pin(descriptor=make_descriptor(size_bytes=len(CONTENT))))


def test_second_ensure_skips_rehashing_a_verified_asset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fast path is the fix: a warm, unchanged cache must not re-read 9.5 GB."""
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    manager.ensure(make_pin())  # first ensure downloads and hashes

    def explode(path: Path) -> str:
        raise AssertionError(f"re-hashed {path.name} although the marker still matches")

    monkeypatch.setattr(manager_module, "sha256_of", explode)
    assert manager.ensure(make_pin()) == [str(tmp_path / "acme--model" / "abc123" / "weights.bin")]


def test_changed_file_under_an_unchanged_marker_is_still_caught(tmp_path: Path) -> None:
    """The integrity contract: content changed, marker untouched -> still verified."""
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    manager.ensure(make_pin())
    marker = tmp_path / "acme--model" / "abc123" / manager_module._VERIFICATION_MARKER_FILENAME
    before = marker.read_text(encoding="utf-8")
    (tmp_path / "acme--model" / "abc123" / "weights.bin").write_bytes(b"tampered-content")
    assert marker.read_text(encoding="utf-8") == before  # the marker really is stale
    with pytest.raises(ChecksumMismatchError):
        manager.ensure(make_pin())


def test_resized_file_is_caught_even_with_the_mtime_restored(tmp_path: Path) -> None:
    """Same timestamp, different size: still a change the marker must not vouch for."""
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    manager.ensure(make_pin())
    target = tmp_path / "acme--model" / "abc123" / "weights.bin"
    stat = target.stat()
    target.write_bytes(CONTENT + b"-extra")
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ChecksumMismatchError):
        manager.ensure(make_pin())


def test_marker_never_vouches_for_a_different_pin(tmp_path: Path) -> None:
    """A marker whose recorded digest differs from the pin must not be trusted."""
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    manager.ensure(make_pin())
    other = make_pin(expected_sha256={"weights.bin": hashlib.sha256(b"something else").hexdigest()})
    with pytest.raises(ChecksumMismatchError):
        manager.ensure(other)


def test_marker_from_another_revision_is_ignored(tmp_path: Path) -> None:
    """Markers are per revision; a new revision verifies from scratch."""
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    manager.ensure(make_pin())
    manager.ensure(make_pin(descriptor=make_descriptor(model_revision="def456")))  # downloads into its own directory
    assert sorted(path.name for path in (tmp_path / "acme--model").iterdir() if path.is_dir()) == ["abc123", "def456"]
    assert (tmp_path / "acme--model" / "abc123" / manager_module._VERIFICATION_MARKER_FILENAME).is_file()
    assert (tmp_path / "acme--model" / "def456" / manager_module._VERIFICATION_MARKER_FILENAME).is_file()


def test_corrupt_marker_falls_back_to_full_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A damaged marker is ignored, never trusted — and never crashes the run."""
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    manager.ensure(make_pin())
    (tmp_path / "acme--model" / "abc123" / manager_module._VERIFICATION_MARKER_FILENAME).write_text("{not json", encoding="utf-8")
    hashed: list[str] = []
    monkeypatch.setattr(manager_module, "sha256_of", lambda path: hashed.append(path.name) or DIGEST)
    manager.ensure(make_pin())
    assert hashed == ["weights.bin"]  # verified the slow way, exactly as before the marker existed


def test_marker_naming_another_revision_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A marker whose recorded revision differs must not verify this revision."""
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    manager.ensure(make_pin())
    marker = tmp_path / "acme--model" / "abc123" / manager_module._VERIFICATION_MARKER_FILENAME
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["model_revision"] = "999999"
    marker.write_text(json.dumps(payload), encoding="utf-8")
    hashed: list[str] = []
    monkeypatch.setattr(manager_module, "sha256_of", lambda path: hashed.append(path.name) or DIGEST)
    manager.ensure(make_pin())
    assert hashed == ["weights.bin"]


def test_marker_of_an_older_record_version_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A marker shape we no longer understand is treated as absent."""
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    manager.ensure(make_pin())
    marker = tmp_path / "acme--model" / "abc123" / manager_module._VERIFICATION_MARKER_FILENAME
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["record_version"] = manager_module._MARKER_RECORD_VERSION + 99
    marker.write_text(json.dumps(payload), encoding="utf-8")
    hashed: list[str] = []
    monkeypatch.setattr(manager_module, "sha256_of", lambda path: hashed.append(path.name) or DIGEST)
    manager.ensure(make_pin())
    assert hashed == ["weights.bin"]


def test_deleted_file_is_caught_even_though_the_marker_verified_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The file vanished after verification -> not the marker's word to vouch for it."""
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    manager.ensure(make_pin())
    (tmp_path / "acme--model" / "abc123" / "weights.bin").unlink()
    monkeypatch.setattr(manager_module, "sha256_of", lambda path: DIGEST)
    manager.ensure(make_pin())  # re-downloads, then verifies the fresh copy
    assert (tmp_path / "acme--model" / "abc123" / "weights.bin").is_file()


def test_inspect_cache_counts_only_bytes_physically_stored(tmp_path: Path) -> None:
    """Aliases are never counted: a file symlink is skipped, a directory symlink is not descended."""
    revision_dir = tmp_path / "acme--model" / "abc123"
    revision_dir.mkdir(parents=True)
    (revision_dir / "weights.bin").write_bytes(CONTENT)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "other.bin").write_bytes(b"z" * 100)
    try:
        (outside / "broken").symlink_to(tmp_path / "nowhere")
        (revision_dir / "aliased.bin").symlink_to(outside / "other.bin")
        (revision_dir / "linked-dir").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this platform needs extra privileges to create symlinks")
    report = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader()).inspect_cache()
    asset = report.assets[0]
    assert [f.filename for f in asset.files] == ["aliased.bin", "weights.bin"]
    assert asset.total_bytes == len(CONTENT)
    assert report.total_bytes == len(CONTENT)


def test_inspect_cache_does_not_list_the_verification_marker(tmp_path: Path) -> None:
    """The marker is manager bookkeeping: it must not appear as a cached asset file, nor add bytes."""
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    manager.ensure(make_pin())
    report = manager.inspect_cache()
    assert [file.filename for asset in report.assets for file in asset.files] == ["weights.bin"]
    assert report.total_bytes == len(CONTENT)


def test_disk_space_gate_is_unchanged_by_a_verified_marker(tmp_path: Path) -> None:
    """A verified cache needs no headroom; a download still demands it."""
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader(), min_free_bytes=1 << 60)
    manager.ensure(make_pin())
    manager.ensure(make_pin())  # verified: no gate, no re-hash
    with pytest.raises(InsufficientDiskSpaceError):
        manager.ensure(make_pin(descriptor=make_descriptor(model_revision="def456", size_bytes=1 << 40)))


def test_ensure_checksum_mismatch_raises(tmp_path: Path) -> None:
    pin = make_pin(expected_sha256={"weights.bin": "0" * 64})
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    with pytest.raises(ChecksumMismatchError) as exc_info:
        manager.ensure(pin)
    assert exc_info.value.expected_sha256 == "0" * 64
    assert exc_info.value.actual_sha256 == DIGEST


def test_ensure_skips_files_without_pinned_checksum(tmp_path: Path) -> None:
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    paths = manager.ensure(make_pin(expected_sha256={}))
    assert Path(paths[0]).read_bytes() == CONTENT


def test_marker_fast_path_tolerates_a_pin_without_checksums(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unpinned file carries no integrity contract, so a verified marker still holds.

    The fast path walks the pin's files, finds a file the pin gives no digest for,
    and skips it rather than reading the missing pin as a change.
    """
    manager = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader())
    pin = make_pin(expected_sha256={})
    manager.ensure(pin)

    hashed: list[str] = []
    monkeypatch.setattr(manager_module, "sha256_of", lambda path: hashed.append(path.name) or DIGEST)
    assert manager.ensure(pin) == [str(tmp_path / "acme--model" / "abc123" / "weights.bin")]
    assert hashed == []  # nothing to re-check, so nothing was read


def test_ensure_announces_the_download_before_fetching(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """One INFO line (model, size, destination) exists before the first fetch call."""
    announced: list[bool] = []

    class _WatchingDownloader(FakeDownloader):
        def download(self, model_id: str, revision: str, filename: str, dest_dir: str) -> str:
            announced.append(any("downloading" in record.getMessage() for record in caplog.records))
            return super().download(model_id, revision, filename, dest_dir)

    caplog.set_level(logging.INFO, logger="parsecraft.assets")
    manager = AssetManager(cache_dir=tmp_path, downloader=_WatchingDownloader())
    manager.ensure(make_pin(descriptor=make_descriptor(size_bytes=5 * 1024**3)))
    assert announced == [True]  # the notice was already there when the fetch ran
    notices = [record.getMessage() for record in caplog.records if "downloading" in record.getMessage()]
    assert notices == [f"downloading acme/model (5.0 GiB) into {tmp_path / 'acme--model' / 'abc123'}"]

    # A second ensure with the files cached fetches (and therefore announces) nothing.
    manager.ensure(make_pin(descriptor=make_descriptor(size_bytes=5 * 1024**3)))
    assert [record.getMessage() for record in caplog.records if "downloading" in record.getMessage()] == notices


def make_pin(**overrides: Any) -> AssetPin:
    values: dict[str, Any] = {
        "descriptor": make_descriptor(),
        "filenames": ["weights.bin"],
        "expected_sha256": {"weights.bin": DIGEST},
    }
    values.update(overrides)
    return AssetPin(**values)


def make_descriptor(**overrides: Any) -> ModelAssetDescriptor:
    values: dict[str, Any] = {
        "model_id": "acme/model",
        "model_revision": "abc123",
        "model_license": "apache-2.0",
        "code_license": "apache-2.0",
        "asset_license": "cc-by-4.0",
    }
    values.update(overrides)
    return ModelAssetDescriptor(**values)


# ── Hugging Face adapter: lazy module boundary ──────────────────────────────


def test_default_downloader_loads_huggingface_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    module = import_huggingface(monkeypatch)
    downloader = default_downloader()

    assert isinstance(downloader, module.HuggingFaceDownloader)


def test_default_downloader_maps_missing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_import_error(name: str) -> Any:
        raise ImportError(f"No module named {name!r}")

    monkeypatch.setattr("parsecraft.assets.downloader.import_module", raise_import_error)
    with pytest.raises(DownloaderUnavailableError, match="download' extra"):
        default_downloader()


def test_manager_defaults_to_factory_downloader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeModule:
        class HuggingFaceDownloader:  # noqa: N801 — mirrors the real adapter shape
            pass

    monkeypatch.setattr("parsecraft.assets.downloader.import_module", lambda name: FakeModule)
    manager = AssetManager(cache_dir=tmp_path)  # default factory path, seam patched — no heavy import
    assert isinstance(manager.downloader, FakeModule.HuggingFaceDownloader)


def test_huggingface_downloader_delegates_to_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    huggingface = import_huggingface(monkeypatch)
    calls: list[dict[str, str]] = []

    def fake_hf_hub_download(**kwargs: str) -> str:
        calls.append(kwargs)
        target = Path(kwargs["local_dir"]) / kwargs["filename"]
        target.write_bytes(CONTENT)
        return str(target)

    monkeypatch.setattr(huggingface, "hf_hub_download", fake_hf_hub_download)
    result = huggingface.HuggingFaceDownloader.download("acme/model", "abc123", "weights.bin", str(tmp_path))
    assert result == str(tmp_path / "weights.bin")
    assert calls == [{"repo_id": "acme/model", "filename": "weights.bin", "revision": "abc123", "local_dir": str(tmp_path)}]


def test_github_downloader_writes_the_pinned_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, float]] = []
    monkeypatch.setattr("urllib.request.urlopen", _record_urlopen(calls))

    result = GitHubDownloader.download("tesseract-ocr/tessdata_fast", "4.1.0", "eng.traineddata", str(tmp_path))

    assert result == str(tmp_path / "eng.traineddata")
    assert (tmp_path / "eng.traineddata").read_bytes() == CONTENT
    url, timeout = calls[0]
    assert url == "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/eng.traineddata"
    assert timeout > 0


def test_github_downloader_percent_encodes_each_segment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A repo id keeps its ``/`` separators; every other segment is fully encoded.

    Encoding the filename's own ``/`` matters: it is what stops a ``../`` in a
    pinned name from surviving as a path separator on the wire, independently of
    the cache-directory check.
    """
    calls: list[tuple[str, float]] = []
    monkeypatch.setattr("urllib.request.urlopen", _record_urlopen(calls))

    GitHubDownloader.download("acme/repo", "a b", "sub dir/../file.bin", str(tmp_path))

    url, _ = calls[0]
    assert url == "https://raw.githubusercontent.com/acme/repo/a%20b/sub%20dir%2F..%2Ffile.bin"
    assert url.split("/")[3:5] == ["acme", "repo"]


def test_github_downloader_rejects_a_path_that_escapes_the_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pinned filename may never write outside the cache directory."""
    calls: list[tuple[str, float]] = []
    monkeypatch.setattr("urllib.request.urlopen", _record_urlopen(calls))

    with pytest.raises(AssetError, match="escapes the cache directory"):
        GitHubDownloader.download("acme/repo", "r1", "../escaped.bin", str(tmp_path))

    assert calls == []
    assert not (tmp_path.parent / "escaped.bin").exists()


def _record_urlopen(calls: list[tuple[str, float]], payload: bytes = CONTENT):  # noqa: ANN202 - local stub
    def fake_urlopen(url: str, timeout: float) -> _FakeResponse:
        calls.append((url, timeout))
        return _FakeResponse(payload)

    return fake_urlopen


def test_github_downloader_maps_a_transfer_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed transfer is typed, and leaves no partial file behind."""

    def failing_urlopen(url: str, timeout: float) -> _FakeResponse:
        raise OSError("connection reset")

    monkeypatch.setattr("urllib.request.urlopen", failing_urlopen)

    with pytest.raises(AssetError, match="could not download"):
        GitHubDownloader.download("acme/repo", "r1", "weights.bin", str(tmp_path))

    assert list(tmp_path.iterdir()) == []


def test_downloader_for_github_needs_no_download_extra() -> None:
    """The GitHub adapter is stdlib-only, so a GitHub-sourced asset installs without it."""
    downloader = downloader_for(ModelSource.GITHUB)

    assert isinstance(downloader, GitHubDownloader)
    assert "huggingface_hub" not in sys.modules


def test_downloader_for_keeps_the_hub_as_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    module = import_huggingface(monkeypatch)

    assert isinstance(downloader_for(ModelSource.HUGGINGFACE), module.HuggingFaceDownloader)


def import_huggingface(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import the heavy adapter against a fake ``huggingface_hub``.

    The ``download`` extra is absent in the light dev/CI env, so the module
    (whose top-level import is the sanctioned heavy-module boundary, like
    ``backends/*/_impl``) can only load against a stub — installed BEFORE
    the import, per test, through ``sys.modules``. The seam used by
    ``default_downloader``'s ``importlib`` boundary is untouched.
    """
    fake_hub = ModuleType("huggingface_hub")
    fake_hub.hf_hub_download = _hub_sentinel  # ty: ignore[unresolved-attribute] — fake module
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
    # Drop any cached adapter so it (re)binds against the stub; monkeypatch
    # restores the absence at teardown.
    monkeypatch.delitem(sys.modules, "parsecraft.assets.huggingface", raising=False)
    return importlib.import_module("parsecraft.assets.huggingface")


def _hub_sentinel(**kwargs: str) -> str:
    raise AssertionError("the fake hub must never be called directly")


def test_model_source_defaults_to_the_hub() -> None:
    """A descriptor written before the discriminator existed keeps its meaning."""
    descriptor = ModelAssetDescriptor(
        model_id="acme/model",
        model_revision="r1",
        model_license="Apache-2.0",
        code_license="Apache-2.0",
        asset_license="none",
    )

    assert descriptor.model_source is ModelSource.HUGGINGFACE


# ── import hygiene ─────────────────────────────────────────────────────────


def test_assets_import_is_clean_without_heavy_modules() -> None:
    script = textwrap.dedent(
        """
        import sys

        import parsecraft.assets
        import parsecraft.assets.downloader
        import parsecraft.assets.manager

        heavy = [m for m in ("huggingface_hub", "torch", "transformers", "vllm", "docling") if m in sys.modules]
        assert not heavy, f"heavy modules imported by parsecraft.assets: {heavy}"
        assert not hasattr(parsecraft.assets, "HuggingFaceDownloader"), "adapter leaked into package namespace"
        print("ASSETS_IMPORTS_OK")
        """
    )
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ASSETS_IMPORTS_OK" in proc.stdout
