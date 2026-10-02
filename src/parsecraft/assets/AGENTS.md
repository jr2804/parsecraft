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
- `models.py` — `AssetPin`, `LicenseAcceptance`, cache report records, and
  `human_bytes` (the one size formatter, reused by `cli.models`).
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
  <dir>` INFO record on the `parsecraft.assets` logger, emitted BEFORE the
  first file request (model + size + destination, no prompt, no flag). The CLI
  routes that channel to stderr (`cli.output.ensure_asset_info_logging`); the
  logger is never in `cli.verbosity.QUIET_LOGGERS` — it is our own message.

## Verification

`mise test` — `tests/test_assets.py` (100% coverage gate applies).
