# AGENTS.md — src/parsecraft/backends/pandoc/

## Purpose

Office/ODF/EPUB/RTF conversion through the system pandoc binary, behind the
optional `pandoc` extra (ADR-0005: GPL-2.0-or-later binary, MIT wrapper,
user's licence decision — never core, never `mise dev`/CI).

## Ownership

- `pandoc.py` — light factory, `PANDOC_FORMATS`, `PANDOC_READERS`
  (MIME → pandoc reader), `DESCRIPTOR`; entry point
  `parsecraft.backends.pandoc.pandoc:factory`.
- `_impl.py` — heavy implementation (`pypandoc` top-level import; loaded via
  `importlib` only at instantiation).

## Local Contracts

- **Declared formats are verified, not guessed:** docx/odt/pptx/rtf/epub
  round-trips and xlsx reads were checked against a real pandoc 3.11 binary
  (2026-09-28); `ods`/`odp` are unsupported by pandoc and deliberately
  absent. Re-verify against the binary before extending `PANDOC_FORMATS`.
- **Single logical page:** `supports_page_ranges`/`supports_multi_page` are
  off; a `page_range` starting beyond page 1 is a typed `INVALID_INPUT`
  record. Cancellation/deadline are checked before the blocking conversion,
  the output budget after it; a failed conversion is a typed
  `BACKEND_ERROR`, never a raise.
- **Missing extra vs missing binary** both surface as
  `DependencyUnavailableError` at `registry.create` (extra names
  `pypandoc`/`pandoc`; the binary is a system install, e.g.
  `mise use pandoc@3`).
- **`rows` stays `None`** (pc-4u7.40): pandoc's path is text export — no
  cheap grid structure, so table chunks carry Markdown `content` only.

## Verification

`mise test` — `tests/test_backends_pandoc.py` (offline, `pypandoc` stubbed;
one real docx round-trip test skips unless the extra is installed), 100%
coverage gate.
