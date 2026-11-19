r"""Where parsecraft keeps its files — the one directory policy.

Two roots, both home-relative on EVERY platform:

- ``~/.cache/parsecraft`` — model downloads, the conversion cache, and the
  runtime scratch space. Disposable: dropping it costs a re-download.
- ``~/.config/parsecraft`` — the global configuration and the recorded licence
  acceptances. Durable: dropping it costs a re-acceptance.

The OS application-data directory is deliberately NOT used: on Windows
``%LOCALAPPDATA%\parsecraft`` buries the files several levels deep in the user
profile where nobody finds them, and the same shape on every platform keeps
"where did parsecraft put this" answerable at a glance.

Every consumer resolves its directory through this module — no second table,
no per-package ``platformdirs`` call. The one ``platformdirs`` use here is a
migration probe, :func:`legacy_models_cache_dir`, which names the place
older versions wrote to so their contents can still be read.
"""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_cache_path

APP_NAME = "parsecraft"
CACHE_DIRNAME = ".cache"
CONFIG_DIRNAME = ".config"


def conversions_cache_dir() -> Path:
    """Conversion cache: ``~/.cache/parsecraft/conversions``."""
    return cache_root() / "conversions"


def temp_dir() -> Path:
    """Runtime scratch space: ``~/.cache/parsecraft/tmp`` (created on demand)."""
    path = cache_root() / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def global_config_file() -> Path:
    """Global configuration file: ``~/.config/parsecraft/config.toml``."""
    return config_root() / "config.toml"


def config_root() -> Path:
    """Root of the durable storage: ``~/.config/parsecraft``."""
    return Path.home() / CONFIG_DIRNAME / APP_NAME


def legacy_models_cache_dir() -> Path | None:
    """Where versions before 2026.11 kept the model cache, or ``None``.

    A migration probe, never a live directory: it names the platform
    application-data location older versions used, so a licence-acceptance
    store written there is still found. ``None`` means that location is the
    current one (Linux with the default XDG cache) and there is nothing
    separate to look for.
    """
    legacy = user_cache_path(APP_NAME) / "models"
    if legacy == models_cache_dir():
        return None
    return legacy


def models_cache_dir() -> Path:
    """Managed model-asset cache: ``~/.cache/parsecraft/models``."""
    return cache_root() / "models"


def cache_root() -> Path:
    """Root of the disposable storage: ``~/.cache/parsecraft``."""
    return Path.home() / CACHE_DIRNAME / APP_NAME
