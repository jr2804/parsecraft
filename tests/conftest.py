"""Pytest configuration and fixtures for ParseCraft."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.documents import DocumentFactory

_NETWORK_MARKER = "network"
_CORPUS_MARKER = "corpus"
_GPU_MARKER = "gpu"
_RUN_DOWNLOADS = "--run-downloads"
_RUN_CORPUS = "--run-corpus"
_RUN_GPU = "--run-gpu"

_test_dir = Path(__file__).parent


@pytest.fixture(scope="session")
def data_dir() -> Path:
    """Return the path to the test data directory."""
    return _test_dir / "data"


@pytest.fixture(scope="session")
def doc_factory(cache_subdir: Path) -> DocumentFactory:
    """Deterministic synthetic documents (.txt/.md/.csv/.json/.html/.pdf) in the cache."""
    return DocumentFactory(cache_subdir)


@pytest.fixture(scope="session")
def cache_subdir(request: pytest.FixtureRequest, subdir: str) -> Path:
    """Return a subdirectory in the pytest cache directory.

    Can be used by other fixtures to easily get a cache directory.
    """
    return Path(request.config.cache.mkdir(subdir))


@pytest.fixture(scope="session")
def subdir() -> str:
    """Default cache subdir for ``cache_subdir`` — override locally to target another one."""
    return "documents"


@pytest.fixture(scope="session")
def downloads_dir() -> Path:
    """Content-addressed staging directory for real corpus documents.

    Deliberately NOT ``cache_dir`` (``tests/test-cache``): that folder is
    pytest-owned and must never be written to directly. Corpus documents are
    large, long-lived artifacts fetched by the corpus tier or staged by hand,
    so they live in this project-owned, gitignored directory instead.
    """
    path = _test_dir / "downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the opt-in gates for network and corpus tests."""
    parser.addoption(
        _RUN_DOWNLOADS,
        action="store_true",
        default=False,
        help=f"run tests marked '{_NETWORK_MARKER}' (they access the network)",
    )
    parser.addoption(
        _RUN_CORPUS,
        action="store_true",
        default=False,
        help=f"run the slow '{_CORPUS_MARKER}' tier (implies {_RUN_DOWNLOADS}; cold cache ≤ 15 min)",
    )
    parser.addoption(
        _RUN_GPU,
        action="store_true",
        default=False,
        help=f"run the '{_GPU_MARKER}' tier (CUDA host + isolated .venv-gpu; also allows network for weight downloads)",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register project markers (pyproject.ini_options is not ours to edit)."""
    config.addinivalue_line(
        "markers",
        f"{_NETWORK_MARKER}: needs network; skipped unless {_RUN_DOWNLOADS} or {_RUN_CORPUS} is passed",
    )
    config.addinivalue_line(
        "markers",
        f"{_CORPUS_MARKER}: slow corpus tier; skipped unless {_RUN_CORPUS} is passed",
    )
    config.addinivalue_line(
        "markers",
        f"{_GPU_MARKER}: needs a CUDA host and the isolated .venv-gpu; skipped unless {_RUN_GPU} is passed",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip network/corpus tests unless opted in, so ``mise test`` stays offline and fast."""
    network_allowed = config.getoption(_RUN_DOWNLOADS) or config.getoption(_RUN_CORPUS) or config.getoption(_RUN_GPU)
    corpus_allowed = config.getoption(_RUN_CORPUS)
    gpu_allowed = config.getoption(_RUN_GPU)
    skip_network = pytest.mark.skip(reason=f"network tests are opt-in: pass {_RUN_DOWNLOADS}")
    skip_corpus = pytest.mark.skip(reason=f"corpus tier is opt-in: pass {_RUN_CORPUS}")
    skip_gpu = pytest.mark.skip(reason=f"gpu tier is opt-in: pass {_RUN_GPU} (CUDA host + .venv-gpu)")
    for item in items:
        if _NETWORK_MARKER in item.keywords and not network_allowed:
            item.add_marker(skip_network)
        if _CORPUS_MARKER in item.keywords and not corpus_allowed:
            item.add_marker(skip_corpus)
        if _GPU_MARKER in item.keywords and not gpu_allowed:
            item.add_marker(skip_gpu)
