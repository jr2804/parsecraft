"""Example third-party ParseCraft backend — entry-point registration.

This module is the entry-point target and MUST stay light: discovery imports
it during ``list_backends()``. Heavy work happens inside ``factory(config)``
(:mod:`parsecraft_example_backend.impl` is imported only then).

Install with ``uv pip install examples/third_party_backend`` and the registry
picks it up via the ``parsecraft.backends`` entry-point group — no change to
ParseCraft source.
"""

from __future__ import annotations

from parsecraft.backends import (
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    DocumentBackend,
)
from parsecraft_example_backend.impl import EchoBackend  # lazy by contract

DESCRIPTOR = BackendDescriptor(
    name="example-echo",
    capabilities=BackendCapabilities(
        supported_formats=["text"],
        supports_page_ranges=True,
        supports_multi_page=True,
        requires_gpu=False,
    ),
)


class EchoFactory:
    """Backend factory — the only sanctioned heavy-import boundary."""

    descriptor: BackendDescriptor = DESCRIPTOR

    def __call__(self, config: BackendConfig) -> DocumentBackend:

        return EchoBackend(config, DESCRIPTOR.capabilities)


factory = EchoFactory()
