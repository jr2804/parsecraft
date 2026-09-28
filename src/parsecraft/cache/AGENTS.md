# AGENTS.md — src/parsecraft/cache/

Content-addressed conversion cache: keyed storage for executed
`DocumentResult`s.

## Purpose

`ConversionCache` stores the aggregated IR of a completed conversion under a
key the pipeline computes (`sha256(source bytes + registry.fingerprint() +
canonical constraints + effective judge identity)`), so repeat conversions
return identical IR without dispatching any backend.

## Ownership

- `store.py` — `ConversionCache` (get/put/entries/total_bytes/clear/sweep),
  `default_cache_root()`, `CacheEntryInfo`, `CACHE_SCHEMA_VERSION`.
- `__init__.py` — curated re-exports.

## Local Contracts

- **Key is built by the pipeline seam** (`pipeline.executor._cache_key`);
  this package never imports routing/registry. The seam protocol is
  `pipeline.executor.CacheProtocol` — `execute(..., cache=None)` keeps the
  previous behaviour exactly; `parsecraft.pipeline` never imports this
  package.
- **Versioned envelope:** `{"schema_version", "key", "result"}`. A schema
  bump, a wrong key, truncation, invalid JSON, or an invalid payload is a
  **miss, never an error** — reads are total (`get` returns `None`).
- **Atomic writes:** temp file (`.key.pid.tmp`) + `os.replace`; keys are
  validated `^[0-9a-f]{64}$` (anything else raises in `put`, misses in
  `get` — no path traversal).
- **Location:** `$PARSECRAFT_CACHE_DIR` or
  `platformdirs.user_cache_path("parsecraft") / "conversions"`; never
  `tests/test-cache/` (pytest-owned — see `tests/AGENTS.md`).
- **No timestamps in payloads** beyond what the IR itself carries; entry
  listing is sorted by key for stable inspect output.
- `sweep(max_bytes)` is an oldest-by-mtime cap only (ponytail: no LRU/TTL —
  upgrade path is a policy object); `put` does not auto-evict.
- `put` failures (disk full, permissions) propagate — a cache that cannot
  store must not silently pretend it did.

## Verification

`mise test` — `tests/test_cache.py` plus the seam tests in
`tests/test_pipeline.py` (100% coverage gate; tmp dirs only, offline).
