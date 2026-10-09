# AGENTS.md — src/parsecraft/assets/

Model-asset manager: pinned downloads, local cache, license-acceptance records.

## Purpose

Download and validate pinned model weights/assets on demand (Hugging Face Hub
via the optional `download` extra), inspect and clean the local cache, and
record explicit license acceptances. Metadata for one asset is
`ModelAssetDescriptor` from `parsecraft.backends.protocol` — reused, never
duplicated; integrity data (per-file SHA-256) lives in `AssetPin`.

## Ownership

- `manager.py` — `AssetManager` (ensure/inspect/remove/clear, license store,
  disk-space and offline checks, checksum verification, the first-use
  download notice), `slug`, `sha256_of`.
- `downloader.py` — `Downloader` protocol + `HuggingFaceDownloader` adapter.
- `models.py` — `AssetPin`, `LicenseAcceptance`, cache report records,
  `VerificationMarker`/`VerifiedFile`, and `human_bytes` (the one size
  formatter, reused by `cli.models`).
- `errors.py` — typed `AssetError` hierarchy.

## Local Contracts

- No fetch and no heavy import (`huggingface_hub`) at module import time;
  heavy imports happen inside methods only (offline-import gate).
- Never commit or bundle model weights; nothing under this package is data.
- Default test path is offline: tests inject a fake `Downloader`; the real
  adapter is exercised only through lazy-import failure/success seams.
- Downloads must be resumable and checksum-verified; a mismatch raises
  `ChecksumMismatchError` — never a silent pass.
- `requires_user_acceptance` is a hard gate: `ensure` raises
  `LicenseNotAcceptedError` until `accept_license` records acceptance
  (timestamped, stored in the cache dir as JSON).
- Offline mode raises `OfflineModeError` for missing files; it never downloads.
- A first-use fetch announces itself: one `downloading <model> (<size>) into
  <dir>` INFO record on the `parsecraft.assets.manager` logger (channel installed on the parent `parsecraft.assets`), emitted BEFORE the
  first file request (model + size + destination, no prompt, no flag). The CLI
  routes that channel to stderr (`cli.output.ensure_asset_info_logging`); the
  logger is never in `cli.verbosity.QUIET_LOGGERS` — it is our own message.
- Warm-cache verification is marker-based: after a full checksum pass the
  manager records each verified file's digest + size + `st_mtime_ns` in
  `.parsecraft-verification.json` inside the revision dir, and `ensure` skips
  re-hashing only when that recorded digest still equals the pin's expected
  digest AND the file's size and mtime are unchanged. The marker is a
  verification cache, never a trust root: it is ignored when missing, damaged,
  foreign, from another revision, or when any pinned file's stamp or digest
  disagrees — every such case falls back to the full re-hash, so a changed file
  under an unchanged marker is still caught. It is written atomically (a torn
  marker is ignored) and excluded from `inspect`/`models list` output.
- `inspect_cache`'s `total_bytes` aggregates the whole revision tree while
  `CachedAsset.files` lists direct children only (its contract) — a backend may
  nest its weights, as MinerU does into `$MINERU_HOME/models/`. `models list`
  shows that measured size for a cached revision, the declared estimate
  otherwise.

## Verification

`mise test` — `tests/test_assets.py` (100% coverage gate applies).
