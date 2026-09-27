"""Offline tests for ``parsecraft models`` (asset cache inspection/management)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner, Result

import parsecraft.cli.models as models_module
from parsecraft.assets import (
    AssetError,
    AssetManager,
    AssetPin,
    DownloaderUnavailableError,
    OfflineModeError,
    slug,
)
from parsecraft.backends import BackendRegistry
from parsecraft.backends.protocol import AssetFilePin, BackendCapabilities, BackendConfig, BackendDescriptor, DocumentBackend, ModelAssetDescriptor
from parsecraft.cli.app import app
from parsecraft.environment import EnvironmentInfo

CONTENT = b"pinned-weights"
DIGEST = hashlib.sha256(CONTENT).hexdigest()

runner = CliRunner()


class FakeDownloader:
    """Writes pinned content into dest_dir; never touches the network."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    def download(self, model_id: str, revision: str, filename: str, dest_dir: str) -> str:
        if self._fail:
            raise DownloaderUnavailableError(model_id, revision, "no download extra")
        target = Path(dest_dir) / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(CONTENT)
        return str(target)


class _NullFactory:
    def __init__(self, descriptor: BackendDescriptor) -> None:
        self.descriptor = descriptor

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        raise NotImplementedError


class _StubManager:
    """Minimal AssetManager stand-in to reach the non-download error branches."""

    offline = False

    def __init__(self, error: AssetError) -> None:
        self._error = error

    def ensure(self, pin: AssetPin) -> list[str]:
        raise self._error


def test_human_bytes_scales() -> None:
    assert models_module.human_bytes(None) == "-"
    assert models_module.human_bytes(512) == "512 B"
    assert models_module.human_bytes(2048) == "2.0 KiB"
    assert models_module.human_bytes(5 * 1024**3) == "5.0 GiB"
    assert models_module.human_bytes(4096 * 1024**4) == "4096.0 TiB"


def test_catalog_skips_backends_without_assets() -> None:
    descriptors = [_descriptor("ocr-demo"), BackendDescriptor(name="native-text", capabilities=BackendCapabilities(supported_formats=["text/plain"]))]
    assert [name for name, _ in models_module.catalog(descriptors)] == ["ocr-demo"]


def test_entries_join_cache_state(tmp_path: Path) -> None:
    asset = _asset()
    manager = _manager(tmp_path)
    _cache_revision(tmp_path, asset)
    items = models_module.entries([_descriptor()], manager.inspect_cache())
    assert [entry.name for entry in items] == ["ocr-demo"]
    assert items[0].cached is True
    assert items[0].estimated_vram_gb == 1.0


def test_resolve_asset_by_name_and_model_id() -> None:
    descriptors = [_descriptor()]
    assert models_module.resolve_asset("ocr-demo", descriptors)[0] == "ocr-demo"
    assert models_module.resolve_asset("acme/model", descriptors)[0] == "ocr-demo"
    with pytest.raises(models_module.CliError) as error:
        models_module.resolve_asset("nope", descriptors)
    assert error.value.exit_code == 2


def test_install_requires_confirmation(tmp_path: Path) -> None:
    with pytest.raises(models_module.CliError, match="without --yes"):
        models_module.install(_asset(), _manager(tmp_path), confirmed=False, accept_license=False)


def test_install_rejects_offline(tmp_path: Path) -> None:
    with pytest.raises(models_module.CliError, match="offline mode"):
        models_module.install(_asset(), _manager(tmp_path, offline=True), confirmed=True, accept_license=False)


def test_install_default_provider_refuses_without_file_pins(tmp_path: Path) -> None:
    with pytest.raises(models_module.CliError, match="no pinned manifest"):
        models_module.install(_asset(), _manager(tmp_path), confirmed=True, accept_license=False)


def test_default_pin_provider_builds_from_file_pins() -> None:
    asset = _asset(file_pins=(AssetFilePin(path="weights.bin", sha256=DIGEST, size=len(CONTENT)),))
    pin = models_module.default_pin_provider(asset)
    assert pin.filenames == ["weights.bin"]
    assert pin.expected_sha256["weights.bin"] == DIGEST
    with pytest.raises(models_module.CliError, match="no pinned manifest"):
        models_module.default_pin_provider(_asset())


def test_install_performs_real_pinned_download(tmp_path: Path) -> None:
    asset = _asset(file_pins=(AssetFilePin(path="weights.bin", sha256=DIGEST, size=len(CONTENT)),))
    paths = models_module.install(asset, _manager(tmp_path), confirmed=True, accept_license=False)
    assert Path(paths[0]).is_file()
    assert Path(paths[0]).read_bytes() == CONTENT


def test_install_license_gate_and_success(tmp_path: Path) -> None:
    asset = _asset(requires_user_acceptance=True)
    manager = _manager(tmp_path)
    with pytest.raises(models_module.CliError, match="--accept-license"):
        models_module.install(asset, manager, confirmed=True, accept_license=False, provider=_pin)

    paths = models_module.install(asset, manager, confirmed=True, accept_license=True, provider=_pin)
    assert Path(paths[0]).is_file()
    assert manager.license_acceptance(asset.model_id, asset.model_revision) is not None


def test_install_download_extra_hint(tmp_path: Path) -> None:
    with pytest.raises(models_module.CliError, match="download extra"):
        models_module.install(_asset(), _manager(tmp_path, fail_download=True), confirmed=True, accept_license=False, provider=_pin)


def test_install_offline_and_generic_asset_errors() -> None:
    asset = _asset()
    offline = cast("AssetManager", _StubManager(OfflineModeError("m", "r", "offline")))
    with pytest.raises(models_module.CliError, match="PARSECRAFT_OFFLINE"):
        _install(offline, asset)
    failing = cast("AssetManager", _StubManager(AssetError("m", "r", "boom")))
    with pytest.raises(models_module.CliError, match="boom"):
        _install(failing, asset)


def _install(manager: AssetManager, asset: ModelAssetDescriptor) -> list[str]:
    return models_module.install(asset, manager, confirmed=True, accept_license=False, provider=_pin)


def _pin(descriptor: ModelAssetDescriptor, filename: str = "weights.bin") -> AssetPin:
    return AssetPin(descriptor=descriptor, filenames=[filename], expected_sha256={filename: DIGEST})


def test_remove_and_clean(tmp_path: Path) -> None:
    asset = _asset()
    manager = _manager(tmp_path)
    assert models_module.remove(asset, manager) == []
    _cache_revision(tmp_path, asset)
    assert models_module.remove(asset, manager) == ["r1"]
    _cache_revision(tmp_path, asset)
    assert models_module.clean(manager) == 1


def test_manager_uses_fallback_downloader(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(models_module, "probe_environment", lambda: EnvironmentInfo(installed_extras=frozenset(), vram_budget_gb=0.0, offline=True))

    def _raise() -> None:
        raise DownloaderUnavailableError(detail="no extra")

    monkeypatch.setattr(models_module, "default_downloader", _raise)
    manager = models_module.manager()
    assert manager.offline is True
    with pytest.raises(DownloaderUnavailableError):
        manager.downloader.download("m", "r", "f", str(tmp_path))


def test_manager_uses_available_downloader(monkeypatch: pytest.MonkeyPatch) -> None:
    downloader = FakeDownloader()
    monkeypatch.setattr(models_module, "default_downloader", lambda: downloader)
    monkeypatch.setattr(models_module, "probe_environment", EnvironmentInfo)
    assert models_module.manager().downloader is downloader


def test_available_descriptors_reads_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = BackendRegistry()
    registry.register("ocr-demo", _NullFactory(_descriptor()))
    monkeypatch.setattr(models_module, "default_registry", registry)
    assert "ocr-demo" in [descriptor.name for descriptor in models_module.available_descriptors()]


def test_render_and_payload(tmp_path: Path) -> None:
    assert models_module.render_entries([]) == ["No model assets registered."]
    asset = _asset()
    _cache_revision(tmp_path, asset)
    items = models_module.entries([_descriptor()], _manager(tmp_path).inspect_cache())
    line = models_module.render_entries(items)[1]
    assert "ocr-demo" in line
    assert line.endswith("yes")
    assert models_module.entries_payload(items)[0]["cached"] is True


def test_cli_models_help() -> None:
    result = runner.invoke(app, ["models", "--help"])
    assert result.exit_code == 0
    for command in ("list", "install", "remove", "clean", "path"):
        assert command in result.output


def test_cli_models_list_text_and_json(cli_state: tuple[AssetManager, ModelAssetDescriptor]) -> None:
    text = runner.invoke(app, ["models", "list"])
    assert text.exit_code == 0
    assert "ocr-demo" in text.output

    as_json = runner.invoke(app, ["models", "list", "--json"])
    assert as_json.exit_code == 0
    assert json_payload(as_json)["model_id"] == "acme/model"


def test_cli_models_list_cached(tmp_path: Path, cli_state: tuple[AssetManager, ModelAssetDescriptor]) -> None:
    _cache_revision(tmp_path, cli_state[1])
    result = runner.invoke(app, ["models", "list", "--json"])
    assert result.exit_code == 0
    assert json_payload(result)["cached"] is True


def test_cli_models_path_text_and_json() -> None:
    text = runner.invoke(app, ["models", "path"])
    assert text.exit_code == 0
    assert "models" in text.output
    as_json = runner.invoke(app, ["models", "path", "--json"])
    assert as_json.exit_code == 0
    assert "location" in as_json.output


def test_cli_models_install_requires_yes_and_refuses_unknown(cli_state: tuple[AssetManager, ModelAssetDescriptor]) -> None:
    result = runner.invoke(app, ["models", "install", "ocr-demo"])
    assert result.exit_code == 2
    assert "without --yes" in _text(result)

    unknown = runner.invoke(app, ["models", "install", "nope", "--yes"])
    assert unknown.exit_code == 2
    assert "unknown model" in _text(unknown)


def test_cli_models_install_success(monkeypatch: pytest.MonkeyPatch, cli_state: tuple[AssetManager, ModelAssetDescriptor]) -> None:
    monkeypatch.setattr(models_module, "install", lambda *args, **kwargs: [str(cli_state[1].model_id)])
    result = runner.invoke(app, ["models", "install", "ocr-demo", "--yes"])
    assert result.exit_code == 0
    assert "installed acme/model" in result.output


def test_cli_models_remove_and_clean(tmp_path: Path, cli_state: tuple[AssetManager, ModelAssetDescriptor]) -> None:
    unknown = runner.invoke(app, ["models", "remove", "nope"])
    assert unknown.exit_code == 2
    assert "unknown model" in _text(unknown)

    assert "is not cached" in runner.invoke(app, ["models", "remove", "ocr-demo"]).output

    _cache_revision(tmp_path, cli_state[1])
    removed = runner.invoke(app, ["models", "remove", "ocr-demo"])
    assert removed.exit_code == 0
    assert "removed acme/model@r1" in removed.output

    _cache_revision(tmp_path, cli_state[1])
    cleaned = runner.invoke(app, ["models", "clean"])
    assert cleaned.exit_code == 0
    assert "removed 1 cached revision(s)" in cleaned.output


def _text(result: Result) -> str:
    return f"{result.output}{result.stderr or ''}"


def _cache_revision(tmp_path: Path, asset: ModelAssetDescriptor) -> None:
    revision_dir = tmp_path / "cache" / slug(asset.model_id) / asset.model_revision
    revision_dir.mkdir(parents=True, exist_ok=True)
    (revision_dir / "weights.bin").write_bytes(CONTENT)


# ── CLI wiring ─────────────────────────────────────────────────────────────


@pytest.fixture
def cli_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[AssetManager, ModelAssetDescriptor]:
    asset = _asset()
    manager = _manager(tmp_path)
    monkeypatch.setattr(models_module, "manager", lambda: manager)
    monkeypatch.setattr(models_module, "available_descriptors", lambda: [_descriptor(asset=asset)])
    return manager, asset


def _descriptor(name: str = "ocr-demo", asset: ModelAssetDescriptor | None = None) -> BackendDescriptor:
    return BackendDescriptor(
        name=name,
        capabilities=BackendCapabilities(supported_formats=["application/pdf"], model_asset=asset if asset is not None else _asset()),
    )


def _asset(
    *,
    model_id: str = "acme/model",
    revision: str = "r1",
    requires_user_acceptance: bool = False,
    model_license: str = "apache-2.0",
    size_bytes: int | None = None,
    quantization: str | None = None,
    estimated_vram_gb: float | None = 1.0,
    file_pins: tuple[AssetFilePin, ...] = (),
) -> ModelAssetDescriptor:
    return ModelAssetDescriptor(
        model_id=model_id,
        model_revision=revision,
        model_license=model_license,
        code_license="MIT",
        asset_license=model_license,
        requires_user_acceptance=requires_user_acceptance,
        size_bytes=size_bytes,
        quantization=quantization,
        estimated_vram_gb=estimated_vram_gb,
        file_pins=file_pins,
    )


def _manager(tmp_path: Path, *, offline: bool = False, fail_download: bool = False) -> AssetManager:
    return AssetManager(cache_dir=tmp_path / "cache", downloader=FakeDownloader(fail=fail_download), offline=offline)


def json_payload(result: Result) -> dict[str, object]:

    payload = json.loads(result.output)
    if isinstance(payload, list):
        return cast("dict[str, object]", payload[0])
    return cast("dict[str, object]", payload)
