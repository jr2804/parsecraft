"""Pytest configuration and fixtures for ParseCraft."""

from __future__ import annotations

import socket
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import NoReturn, Protocol, cast

import pytest

from tests.fixtures.documents import DocumentFactory
from tests.fixtures.jev_sdk import StubSdk

_NETWORK_MARKER = "network"
_CORPUS_MARKER = "corpus"
_GPU_MARKER = "gpu"
_JUDGE_MARKER = "judge"
_RUN_DOWNLOADS = "--run-downloads"
_RUN_CORPUS = "--run-corpus"
_RUN_GPU = "--run-gpu"
_RUN_JUDGE = "--run-judge"

#: Opt-in tiers that legitimately connect. The offline tripwire exempts on the
#: MARKER alone — never on test names or module lists — so the mechanism stays
#: general (pc-mp2).
_NETWORK_EXEMPT_MARKERS = frozenset({_NETWORK_MARKER, _CORPUS_MARKER, _GPU_MARKER, _JUDGE_MARKER})

_test_dir = Path(__file__).parent


class _LooseCall(Protocol):
    """Delegated socket primitive: the wrappers below call the real one loosely."""

    def __call__(self, *args: object, **kwargs: object) -> object: ...


class _HasKeywords(Protocol):
    """What the tripwire reads off ``request.node`` (pytest does not export Node)."""

    keywords: Mapping[str, object]


@pytest.fixture(autouse=True)
def _offline_tripwire(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The default suite must PROVE offline, not merely avoid network by luck (pc-mp2).

    Any real network attempt in an ordinary test raises immediately and names
    the test. The block is socket-level, so httpx, urllib, huggingface_hub and
    the judge SDKs are all covered by one gate; it does not reach
    subprocesses (the offline-import gate blocks network in its own
    subprocess already). Exemption keys on the tier MARKER alone: network /
    judge / corpus / gpu tests only run under their ``--run-*`` flags, so an
    unmarked test can never legitimately connect.
    """
    if _may_reach_network(request.node):
        yield
        return

    real_getaddrinfo = cast("_LooseCall", socket.getaddrinfo)
    real_create_connection = cast("_LooseCall", socket.create_connection)
    real_connect = cast("_LooseCall", socket.socket.connect)

    def _is_local(host: object) -> bool:
        """Loopback and wildcard literals are not the network (pc-mp2)."""
        return not isinstance(host, str) or host == "" or host.lower() in {"localhost", "127.0.0.1", "::1"} or host.startswith("127.")

    def _deny(nodeid: str) -> NoReturn:
        raise AssertionError(
            f"{nodeid} reached the network — mark it '{_NETWORK_MARKER}' (or a tier marker) and run it under the matching --run flag, or stub the call"
        )

    nodeid = request.node.nodeid

    def _forbidden_getaddrinfo(host: object, port: object, *args: object, **kwargs: object) -> object:
        if _is_local(host):
            return real_getaddrinfo(host, port, *args, **kwargs)
        _deny(nodeid)

    def _forbidden_create_connection(address: object, *args: object, **kwargs: object) -> object:
        if isinstance(address, tuple) and address and _is_local(address[0]):
            return real_create_connection(address, *args, **kwargs)
        _deny(nodeid)

    def _forbidden_connect(self: socket.socket, address: object, *args: object, **kwargs: object) -> None:
        if isinstance(address, tuple) and address and _is_local(address[0]):
            real_connect(self, address, *args, **kwargs)
        else:
            _deny(nodeid)

    monkeypatch.setattr(socket, "getaddrinfo", _forbidden_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", _forbidden_create_connection)
    monkeypatch.setattr(socket.socket, "connect", _forbidden_connect)
    yield


def _may_reach_network(item: _HasKeywords) -> bool:
    """Whether ``item`` carries an opt-in tier marker (keyed on the marker alone)."""
    return any(marker in item.keywords for marker in _NETWORK_EXEMPT_MARKERS)


@pytest.fixture
def jev_sdk(monkeypatch: pytest.MonkeyPatch) -> StubSdk:
    """A fake ``typesafe_sdk`` module installed in ``sys.modules``.

    The System One providers import their SDK through ``importlib`` at
    ``load_judge`` time, so installing this stub exercises the real provider
    code offline (see ``tests/fixtures/jev_sdk.py``).
    """
    return StubSdk().install(monkeypatch)


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
    parser.addoption(
        _RUN_JUDGE,
        action="store_true",
        default=False,
        help=f"run the '{_JUDGE_MARKER}' tier (live providers: typesafe-ai/jev-latest, zen/<model>, or the semi-live local ollama/<model>)",
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
    config.addinivalue_line(
        "markers",
        f"{_JUDGE_MARKER}: needs a live judge provider (TYPESAFE/OPENCODE keys, or a local Ollama model); skipped unless {_RUN_JUDGE} is passed",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip network/corpus tests unless opted in, so ``mise test`` stays offline and fast."""
    network_allowed = config.getoption(_RUN_DOWNLOADS) or config.getoption(_RUN_CORPUS) or config.getoption(_RUN_GPU)
    corpus_allowed = config.getoption(_RUN_CORPUS)
    gpu_allowed = config.getoption(_RUN_GPU)
    judge_allowed = config.getoption(_RUN_JUDGE)
    skip_network = pytest.mark.skip(reason=f"network tests are opt-in: pass {_RUN_DOWNLOADS}")
    skip_corpus = pytest.mark.skip(reason=f"corpus tier is opt-in: pass {_RUN_CORPUS}")
    skip_gpu = pytest.mark.skip(reason=f"gpu tier is opt-in: pass {_RUN_GPU} (CUDA host + .venv-gpu)")
    skip_judge = pytest.mark.skip(reason=f"judge tier is opt-in: pass {_RUN_JUDGE} (live judge provider)")
    for item in items:
        if _NETWORK_MARKER in item.keywords and not network_allowed:
            item.add_marker(skip_network)
        if _CORPUS_MARKER in item.keywords and not corpus_allowed:
            item.add_marker(skip_corpus)
        if _GPU_MARKER in item.keywords and not gpu_allowed:
            item.add_marker(skip_gpu)
        if _JUDGE_MARKER in item.keywords and not judge_allowed:
            item.add_marker(skip_judge)
