"""The session offline tripwire (pc-mp2) — the default suite must PROVE offline.

An ordinary test that touches the real network fails immediately with a message
naming it; the tier markers (`network`/`judge`/`corpus`/`gpu`) exempt a test,
keyed on the marker alone. See `tests/conftest.py::_offline_tripwire`.
"""

from __future__ import annotations

import socket

import pytest

from tests.conftest import _may_reach_network


def test_a_default_test_cannot_open_a_socket(request: pytest.FixtureRequest) -> None:
    """An unpatched fetch in an ordinary test FAILS, and the failure names the test.

    The address is TEST-NET-1 (unroutable by design); the tripwire must raise
    before any packet leaves, so this test passes offline by construction.
    """
    with pytest.raises(AssertionError) as excinfo:
        socket.create_connection(("192.0.2.1", 443), timeout=1)

    assert request.node.nodeid in str(excinfo.value)  # the failure names the test
    assert "reached the network" in str(excinfo.value)


def test_the_block_covers_resolution_as_well_as_connect() -> None:
    """DNS lookup alone is network use; getaddrinfo is blocked too."""
    with pytest.raises(AssertionError, match="reached the network"):
        socket.getaddrinfo("example.com", 443)


def test_loopback_is_not_the_network() -> None:
    """Loopback literals pass through — refusing them would break local-transport tests.

    The connection is refused by nothing listening (OSError), proving the
    tripwire did NOT fire for 127.0.0.1.
    """
    with pytest.raises(OSError, match="timed out|refused|10061"):
        socket.create_connection(("127.0.0.1", 1), timeout=0.2)


@pytest.mark.network
def test_a_tier_marker_exempts_the_tripwire(request: pytest.FixtureRequest) -> None:
    """Exemption keys on the MARKER alone (runs under --run-downloads)."""
    assert _may_reach_network(request.node) is True


def test_an_unmarked_item_is_not_exempt(request: pytest.FixtureRequest) -> None:
    """The default case: no tier marker means no exemption."""
    assert _may_reach_network(request.node) is False
