---
title: Quickstart
---

## Inspect the CLI

```bash
uv run parsecraft                    # help (no_args_is_help)
uv run parsecraft default            # welcome message
uv run parsecraft backends           # registered backends
uv run parsecraft backends --json    # machine-readable descriptors
uv run parsecraft --version          # package version
```

`parsecraft backends` loads registered backends lazily and prints any failed
entry point to stderr:

```text
$ uv run parsecraft backends
warning: backend 'broken' failed to load: failed to load backend 'broken' from entry point: ...
example-echo             cpu              text
```

## Build an IR document

The IR is the source of truth. Construct a `DocumentResult` from typed chunks,
then project it to Markdown.

```python exec="yes"
--8<-- "docs/getting-started/quickstart.py"
```

Run it directly:

```bash
uv run python docs/getting-started/quickstart.py
```

## Convert through a backend

A backend turns a source into `PageResult` blocks inside a `BackendResult`.
Register the backend in the process registry, then call `create()` to
instantiate it:

```python
from parsecraft.backends import (
    BackendConfig,
    BackendRegistry,
    ConversionRequest,
    SourceDocument,
)

from parsecraft_example_backend import factory  # pip install examples/third_party_backend

registry = BackendRegistry()
registry.register("example-echo", factory)

backend = registry.create("example-echo", BackendConfig(name="example-echo"))
result = backend.convert(
    ConversionRequest(source=SourceDocument(uri="memory://demo", content=b"hello"))
)
print(result.pages[0].blocks[0].content)
# echo page 1
```

!!! note "Assembly is not implemented yet"
    Backends return `BackendResult` (pages + failures + provenance). Assembling
    a full `DocumentResult` — metadata, cross-page relations, trace — is a
    Phase 1 deliverable. The IR types and the projection already exist.

## Next steps

- [Architecture overview](../architecture/overview.md) — how the pieces fit.
- [Write a backend](../guides/backend-authoring.md) — register your own.
- [CLI reference](../reference/cli.md) — exact commands and flags.
