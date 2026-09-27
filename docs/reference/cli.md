---
title: CLI reference
---

## Synopsis

```text
parsecraft [OPTIONS] COMMAND [ARGS]...
```

The app sets `no_args_is_help=True`: a bare `parsecraft` prints help and exits
with status `2`. Shell completion is enabled (`add_completion=True`).

## Global options

| Option | Alias | Behaviour |
| ------ | ----- | --------- |
| `--version` | `-v` | Print the package version (eager) and exit |
| `--install-completion` | | Install completion for the current shell |
| `--show-completion` | | Print the completion script |
| `--help` | | Show help and exit |

`--version` reads `importlib.metadata.version("parsecraft")` and falls back to
`0.0.0` when the distribution is not installed (for example, a source checkout
run without an install).

## Commands

`parsecraft config` is a command group; run `parsecraft config --help` to list
its subcommands.

### `parsecraft default`

Print the welcome message.

```text
$ parsecraft default
Welcome to ParseCraft!
Use --help to see available commands.
```

### `parsecraft backends`

List registered backends. Discovery runs lazily on first use.

| Option | Env var | Behaviour |
| ------ | ------- | --------- |
| `--json` | `PARSECRAFT_JSON` | Emit machine-readable JSON instead of the table |

Exit status is `0`. Load failures never raise — they are printed to **stderr**
before the table:

```text
$ parsecraft backends
warning: backend 'broken' failed to load: failed to load backend 'broken' from entry point: ...
example-echo             cpu              text
```

Table columns:

| Column | Source |
| ------ | ------ |
| Name (24 columns) | `descriptor.name` |
| Device | `gpu` when `capabilities.requires_gpu`, otherwise `cpu` |
| VRAM | `vram<=XG` when `capabilities.estimated_vram_gb` is set, otherwise blank |
| Formats | Comma-joined `capabilities.supported_formats`, or `-` when empty |

With no backends registered:

```text
$ parsecraft backends
No backends registered.
```

`--json` emits the descriptor list as JSON (`indent=2`, keys sorted). The list
is `[]` when none are registered; the schema is `BackendDescriptor`:

```json
[
  {
    "capabilities": {
      "estimated_vram_gb": null,
      "optional_dependency_group": null,
      "requires_gpu": false,
      "supported_formats": ["text"],
      "supports_multi_page": true,
      "supports_page_ranges": true
    },
    "name": "example-echo"
  }
]
```

### `parsecraft config`

Command group for the layered configuration engine (ADR-0001 §5). Sources are
merged in order `default → global → project → env → cli`. The global file is
`platformdirs.user_config_dir("parsecraft")/config.toml`; the project file is
`./parsecraft.toml`; settings come from `PARSECRAFT_CONFIG_*` env vars (`__`
separates nested sections).

Shared options:

| Option | Env var | Behaviour |
| ------ | ------- | --------- |
| `--config-file PATH` | | Load `PATH` as the project layer instead of `./parsecraft.toml` |
| `--json` | `PARSECRAFT_JSON` | Emit machine-readable JSON |

Recognized settings:

| Key | Type | Default | Notes |
| --- | ---- | ------- | ----- |
| `hf_token` | string | unset | Secret; write `${HF_TOKEN}`, never a literal. Redacted by `show` |
| `cache_dir` | path | unset | Asset-cache override; resolved relative to its declaring file |
| `offline` | bool | `false` | Asset-manager offline mode |
| `min_free_bytes` | int | `0` | Asset-manager free-space floor |

#### `parsecraft config check`

Validate the effective configuration. Exit `1` when invalid, otherwise `0`.
Warnings and errors print on stderr in text mode.

```text
$ parsecraft config check
Configuration is valid.
$ parsecraft config check --json
{
  "errors": [],
  "snapshot": { "cache_dir": null, "hf_token": null, "min_free_bytes": 0, "offline": false },
  "valid": true,
  "warnings": []
}
```

On failure `--json` returns `"valid": false` with an `errors` list.

#### `parsecraft config show`

Print every effective key with its value and provenance (`layer:origin`).
Values are coerced through the schema; `hf_token` is redacted as `***`. When
the configuration is invalid, `show` still prints the raw source values.

```text
$ parsecraft config show
cache_dir = null  (default:<defaults>)
hf_token = "***"  (default:<defaults>)
min_free_bytes = 0  (default:<defaults>)
offline = false  (default:<defaults>)
```

`--json` emits one object per key:
`{"key", "value", "source": {"layer", "origin"}}`.

## Environment variables

| Variable | Used by | Description |
| -------- | ------- | ----------- |
| `PARSECRAFT_JSON` | `parsecraft backends`, `parsecraft config` | Default value of `--json` |
| `PARSECRAFT_CONFIG_*` | `parsecraft config` | Configuration settings (see below) |

`PARSECRAFT_JSON` is a CLI flag default. Configuration settings use the
distinct `PARSECRAFT_CONFIG_` prefix so the flag cannot collide with a setting
key.

## Source

- `src/parsecraft/cli/app.py` — app, callback, command registration
- `src/parsecraft/cli/commands.py` — `default`, `backends`, `config_check`, `config_show`
- `src/parsecraft/cli/args.py` — `JsonFlag`, `ConfigFileOption`, `PARSECRAFT_JSON`
