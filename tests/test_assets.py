"""Offline tests for the asset manager: fake downloader, no network."""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import subprocess
import sys
import textwrap
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from parsecraft.assets import (
    AssetError,
    AssetManager,
    AssetPin,
    ChecksumMismatchError,
    DownloaderUnavailableError,
    InsufficientDiskSpaceError,
    LicenseNotAcceptedError,
    OfflineModeError,
    default_downloader,
    sha256_of,
    slug,
)
from parsecraft.backends.protocol import ModelAssetDescriptor

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


def test_inspect_cache_reports_files_and_skips_subdirs(tmp_path: Path) -> None:
    revision_dir = tmp_path / "acme--model" / "abc123"
    revision_dir.mkdir(parents=True)
    (revision_dir / "weights.bin").write_bytes(CONTENT)
    (revision_dir / "nested").mkdir()
    report = AssetManager(cache_dir=tmp_path, downloader=FakeDownloader()).inspect_cache()
    assert len(report.assets) == 1
    asset = report.assets[0]
    assert asset.model_id == "acme/model"
    assert asset.revision == "abc123"
    assert [f.filename for f in asset.files] == ["weights.bin"]
    assert asset.total_bytes == len(CONTENT)
    assert report.total_bytes == len(CONTENT)


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
