---
title: Write a backend
---

A backend is a separate Python distribution that declares the
`parsecraft.backends` entry point. ParseCraft discovers it at runtime — no
change to this package is needed. The working reference is
[`examples/third_party_backend/`](https://github.com/jr2804/parsecraft/tree/main/examples/third_party_backend).

## 1. Create the distribution

```text
my-backend/
├── pyproject.toml
└── src/my_backend/
    ├── __init__.py     # entry-point target — light
    └── impl.py         # backend + heavy imports — loaded on create()
```

```toml
# pyproject.toml
[project]
name = "my-backend"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["parsecraft"]

[project.entry-points."parsecraft.backends"]
my-backend = "my_backend:factory"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/my_backend"]
```

The entry-point name (`my-backend`) must match the descriptor `name` and the
pattern `^[a-z0-9][a-z0-9_-]*$`.

## 2. Declare descriptor and factory

The entry-point module must stay light. Discovery imports it during
`list_backends()`, so import only the protocol types here; import the
implementation inside `__call__`.

```python
# src/my_backend/__init__.py
from __future__ import annotations

from parsecraft.backends import (
    BackendCapabilities,
    BackendConfig,
    BackendDescriptor,
    DocumentBackend,
)

DESCRIPTOR = BackendDescriptor(
    name="my-backend",
    capabilities=BackendCapabilities(
        supported_formats=["pdf"],
        supports_page_ranges=True,
        supports_multi_page=True,
        requires_gpu=False,
    ),
)


class MyFactory:
    descriptor: BackendDescriptor = DESCRIPTOR

    def __call__(self, config: BackendConfig) -> DocumentBackend:
        from my_backend.impl import MyBackend  # lazy by contract

        return MyBackend(config, DESCRIPTOR.capabilities)


factory = MyFactory()
```

!!! warning "Keep the entry-point module light"
    Discovery loads the entry-point target. Importing a model runtime or CUDA
    stack here makes every `parsecraft backends` call pay that cost — and breaks
    the offline-import guarantee. Heavy imports belong inside `__call__` or
    deeper.

## 3. Implement the backend

```python
# src/my_backend/impl.py
from __future__ import annotations

from parsecraft.backends import (
    AnalysisResult,
    BackendCapabilities,
    BackendConfig,
    BackendRef,
    BackendResult,
    ConversionRequest,
    DocumentBackend,
    PageSignal,
    SourceDocument,
)
from parsecraft.ir import ChunkKind, PageResult, StructuredChunk


class MyBackend:
    name = "my-backend"

    def __init__(self, config: BackendConfig, capabilities: BackendCapabilities) -> None:
        self._config = config
        self.capabilities = capabilities

    def analyze(self, source: SourceDocument) -> AnalysisResult:
        ...  # return source_hash + per-page signals

    def convert(self, request: ConversionRequest) -> BackendResult:
        first = request.page_range.start if request.page_range else 1
        pages = [
            PageResult(
                page_number=first,
                blocks=[
                    StructuredChunk(
                        id=f"my-{first}-b0",
                        kind=ChunkKind.PARAGRAPH,
                        content="converted content",
                        page_number=first,
                        reading_order=0,
                    )
                ],
            )
        ]
        return BackendResult(
            backend=BackendRef(name=self.name, version="0.1.0"),
            pages=pages,
            elapsed_s=0.0,
        )
```

Honor the bounds in `request` — `page_range`, `timeout_s`, `cancellation`,
`max_output_chars`, `max_context_tokens`. When a bound cannot be met, return a
typed `PassFailure` in `BackendResult.failures` instead of overrunning it.

## 4. Install and verify

```bash
uv pip install path/to/my-backend
uv run parsecraft backends
# my-backend               cpu              pdf
```

`parsecraft backends` discovers the entry point. If the module raises during
load, the warning appears on stderr and the backend is recorded in
`registry.load_errors`.

## Checklist

- [ ] Entry-point group is exactly `parsecraft.backends`
- [ ] `descriptor.name` equals the entry-point name and matches `^[a-z0-9][a-z0-9_-]*$`
- [ ] The entry-point module imports no heavy runtime
- [ ] `factory(config)` is the first heavy import
- [ ] `convert()` honors every field in `ConversionRequest`
- [ ] Failures are returned as `PassFailure`, never raised as strings

## Related pages

- [Backends architecture](../architecture/backends.md)
- [IR reference](../architecture/ir.md)
- [Example backend source](https://github.com/jr2804/parsecraft/tree/main/examples/third_party_backend)
