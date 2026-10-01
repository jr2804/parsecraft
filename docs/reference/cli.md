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

### `parsecraft convert`

Convert a document through the auto-mode pipeline: analyze the source, route it
with `parsecraft.routing`, execute with `parsecraft.pipeline`, and print the IR.

```text
parsecraft convert SOURCE [OPTIONS]
```

| Option | Default | Behaviour |
| ------ | ------- | --------- |
| `--backend`, `-b NAME` | unset | Non-auto: lead with this backend (it must be eligible) |
| `--judge SPEC` | unset | Route with a judge provider (`provider/model[:variant]`); mutually exclusive with `--backend`; may use the network |
| `--classifier SPEC` | unset | Fold OCR-need facts from a classifier provider (`provider/model[:variant]`) into the analysis |
| `--auto` / `--no-auto` | `--auto` | Route automatically; `--no-auto` requires `--backend` |
| `--max-passes N` | `1` | Fallback passes per page group (`N >= 1`) |
| `--no-ocr` | off | Forbid OCR backends |
| `--json` | off | Emit the IR as JSON instead of the Markdown projection |
| `--cache` / `--no-cache` | `--no-cache` | Reuse a content-addressed conversion cache |

Output is the Markdown projection (`parsecraft.ir.markdown.to_markdown`) or the
`DocumentResult` as JSON (`--json`). Supported suffixes come from the public
`parsecraft.pipeline.MEDIA_TYPES` map: the text family (`.txt`, `.text`, `.md`,
`.markdown`, `.html`, `.htm`, `.rst`, `.tex`, `.log`, `.ini`, `.cfg`, `.toml`,
`.sh`, `.py`, `.c`, `.cc`, `.cpp`, `.h`, `.hpp`) plus `.pdf`, `.jpg`, `.jpeg`,
`.png`, `.tif`, and `.tiff`. `.csv`, `.json`, and `.xml` are deliberately
unsupported (no backend claims those media types).

```text
$ parsecraft convert report.txt
<!-- page 1 -->

A paragraph comfortably longer than the forty character routing threshold.
```

The command probes the host once per invocation, analyzes with the
deterministic analysis backend (candidates whose optional dependency is
installed or dependency-free win; native backends first, then name order), and
reuses that probe for the routing constraints built by
`parsecraft.environment.constraints_from_environment` (installed extras,
measured VRAM, `PARSECRAFT_OFFLINE`). `--max-passes` and `--no-ocr` map to
`RoutingConstraints.max_passes` and `allow_ocr`.

Both optional routing seams accept a spec string, resolved before any I/O:
`--judge` re-ranks each page's eligible candidates (lazy-loaded from
`parsecraft.providers.<provider>.load_judge`), and `--classifier` folds per-page
OCR-need facts into the analysis ahead of routing (lazy-loaded from
`…load_classifier`). Both default to off, which is exactly the rule-table
behaviour, and `--backend` cannot be combined with `--judge` — both choose the
lead candidate. The one shipped classifier provider is
`pdfinspector/detect_pdf` (needs the `pdf-inspector` extra): a local, model-free
text-layer scan whose verdicts can only add OCR-need, never remove it. The
shipped judge providers are `ollaya/laya` (a local daemon reading
`OLLAYA_BASE_URL`) and `systemone/jev` (TypeSafe's System One API, needs the
`systemone` extra and `TYPESAFE_API_KEY` — resolution exits `1` without it).
`--judge` is an explicit opt-in to whatever that provider does — the System One
provider reaches the network — and is **not** gated by `PARSECRAFT_OFFLINE`,
which excludes model-asset *backends* from candidacy rather than the judge seam.
With no `--judge`/`--classifier` spec, no provider is resolved at all.

Exit codes: `0` success, `1` analysis or routing failure, `2` usage error
(unsupported suffix, missing file, `--no-auto` without `--backend`, a `--backend`
that is not eligible for the source, a malformed `--judge`/`--classifier` spec,
or `--backend` combined with `--judge`). A missing optional backend dependency
also exits `1` with an actionable `optional dependency missing: …` message
naming the extra to install; a spec whose provider cannot be loaded exits `1`
with `judge unavailable: …` or `classifier unavailable: …`.

Eligibility matches `RoutingConstraints.formats` against each backend's
`supported_formats`; both declare MIME media types (`text/plain`,
`application/pdf`, `image/jpeg`, `image/png`), so a scanned PDF can route to
OCR and a digital PDF to `native-pdf`.

With `--cache`, a completed conversion is stored under
`sha256(source bytes + registry.fingerprint() + canonical constraints +
effective judge identity)`, and a later identical conversion returns the stored
`DocumentResult` **without dispatching any backend**. Because nothing ran, a hit's
`PipelineResult.groups` are plan-shaped — the planned pages and candidates with
`winner=None` and `attempts=[]`; read `plan` for the route, not `groups` for
evidence of execution.

The key covers the source bytes, the registered backend set
(`registry.fingerprint()` — names, versions, supported formats), the resolved
`RoutingConstraints`, and the judge identity, so a fingerprint, constraint, or
envelope-schema change is a **miss, never stale reuse**. Caching is **off by
default** and opt-in per invocation; the root is `$PARSECRAFT_CACHE_DIR` or the
platform user-cache directory followed by `parsecraft/conversions`. Inspect or
clear it with [`parsecraft cache`](#parsecraft-cache).

### `parsecraft cache`

Inspect or clear the conversion cache used by `convert --cache`.

| Option | Default | Behaviour |
| ------ | ------- | --------- |
| `--json` | off | Emit `location`, `entries`, `total_bytes`, and the sorted entry `keys` as JSON |
| `--clear` | off | Delete every cached conversion |

By default the command prints the cache location, the number of entries, and
their total size:

```text
$ parsecraft cache
location: /home/me/.cache/parsecraft/conversions
entries: 3
size: 12.4 KiB
```

Each entry is one JSON file named by its `sha256` key. `--json` additionally
lists every key; per-entry sizes come from the library API
(`parsecraft.cache.ConversionCache.entries()` → `key`, `size_bytes`). With
`--clear` the output is the single line
`removed N conversion cache entries from <root>`. The root honours
`PARSECRAFT_CACHE_DIR` and otherwise defaults to the platform user-cache
directory plus `parsecraft/conversions`.

### `parsecraft inspect`

Analyze a source with the cheapest analysis backend and preview the route
without converting content (same backend selection as `convert`).

| Option | Default | Behaviour |
| ------ | ------- | --------- |
| `--json` | off | Emit the analysis and routing preview as JSON |
| `--max-passes N` | `1` | Fallback passes for the previewed plan |
| `--no-ocr` | off | Forbid OCR backends in the preview |

Output lists the media type, source hash, page count, every page signal
(`chars`, `images`, `blank`, `replacement`), any diagnostics, and the routing
preview (`primary`, then per-page `intent`/`chosen`/`candidates` and reason).
When no backend is eligible the line reads `routing: unavailable — <error>` and
the command still exits `0`, because the analysis itself succeeded.

```text
$ parsecraft inspect report.txt
source: text/plain
hash: a5063811dae62ef716743d0c10e2f6e99afc19ff4d8dc53ec04d09d206cf234a
pages: 1
page 1: chars=131 images=0 blank=False replacement=n/a
routing: primary=native-text
  page 1: intent=native chosen=native-text candidates=native-text
    reason: page 1: native text sufficient (text_chars=131); first pass native-text
```

### `parsecraft models`

Command group over `parsecraft.assets.AssetManager`.

| Subcommand | Behaviour |
| ---------- | --------- |
| `models list` | Descriptor catalogue joined with cache state (name, model id, revision, size, license, quant, VRAM est, cached). `--json` supported |
| `models install NAME` | Install a pinned asset; requires `--yes` (network opt-in) and `--accept-license` when the license requires it |
| `models remove NAME` | Delete every cached revision of one model |
| `models clean` | Delete every cached revision |
| `models path` | Print the cache location and total size (`--json` supported) |

`NAME` is a backend name (`ocr-ovis`) or a model id (`ATH-MaaS/OvisOCR2`).
Offline mode comes from the detected host (`PARSECRAFT_OFFLINE`);
`list`/`path`/`remove`/`clean` never touch the network.

!!! note "Pinned manifests"
    `models install` builds the `AssetPin` from the descriptor's pinned
    `file_pins` (repo path + SHA-256 per file) and verifies every file through
    `AssetManager.ensure`. A descriptor with no `file_pins` (a non-model
    backend) produces a typed "no pinned manifest" error (exit `1`).
    Downloading also requires the `download` extra — without it the error names
    the extra and the install command.

### `parsecraft benchmark`

Benchmark every eligible backend over local documents and print a
deterministic report (the harness omits wall-clock metadata).

| Option | Default | Behaviour |
| ------ | ------- | --------- |
| `--json` | off | Print the JSON report instead of Markdown |
| `--markdown` | on | Force the Markdown report (mutually exclusive with `--json`) |
| `--output`, `-o DIR` | unset | Also write `benchmark.json` and `benchmark.md` into `DIR` |
| `--max-passes N` | `1` | Fallback passes per page group |
| `--no-ocr` | off | Forbid OCR backends |

Documents are read locally; a missing or unsupported path becomes a `Skip`
record rather than an error. Constraints come from the detected host
(`constraints_from_environment`) with no format restriction, because the
harness filters candidates by each document's own media type.

```text
$ parsecraft benchmark tests/downloads/report.txt
# ParseCraft benchmark report

package: `2026.9.9`
...
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
| `PARSECRAFT_CACHE_DIR` | `parsecraft convert --cache`, `parsecraft cache` | Overrides the conversion-cache root |

`PARSECRAFT_JSON` is a CLI flag default. Configuration settings use the
distinct `PARSECRAFT_CONFIG_` prefix so the flag cannot collide with a setting
key.

## Source

- `src/parsecraft/cli/app.py` — app, callback, command and group registration
- `src/parsecraft/cli/commands.py` — `backends`, `convert`, `inspect`, `benchmark`, `cache`, `models_*`, `config_*`
- `src/parsecraft/cli/convert.py` — `convert_source`, `PreferredBackendJudge`, seam-spec resolution, rendering
- `src/parsecraft/cli/inspect.py` — `inspect_source`, preview rendering
- `src/parsecraft/cli/benchmark.py` — harness wrapper + report writers
- `src/parsecraft/cli/models.py` — asset catalogue, `PinProvider`, cache management
- `src/parsecraft/cli/errors.py` — `CliError`
- `src/parsecraft/cli/args.py` — `JsonFlag`, `ConfigFileOption`, `PARSECRAFT_JSON`
