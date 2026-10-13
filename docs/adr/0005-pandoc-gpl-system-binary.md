# ADR-0005: pandoc as an optional GPL system-binary backend

- **Status:** Accepted
- **Date:** 2026-09-28
- **Deciders:** user, pc-1
- **Related:** ADR-0001 (dependency licence rule, §4 profiles, §8 no committed
  binaries), ADR-0003 (optional AGPL PyMuPDF — the pattern this follows)

## Context

ADR-0001 §2 admits only permissive licences into the core dependency set;
anything copyleft needs an explicit addendum before it enters any profile.
Office/ODF/EPUB/RTF conversion needs **pandoc**, which has two halves with
different licences: the Python wrapper **pypandoc is MIT**, but the **pandoc
binary is GPL-2.0-or-later**. ParseCraft therefore cannot ship, depend on, or
auto-install pandoc — but it may offer conversion through an optional extra
that makes the copyleft choice explicit, exactly as ADR-0003 does for the
AGPL PyMuPDF binary.

## Decision

1. **pandoc support lives only behind the optional `pandoc` extra**
   (`pandoc = ["pypandoc>=1.15"]`, wrapper only). It is never a core
   dependency and is not installed by `mise dev` or CI.
2. **The pandoc binary is a system dependency, not a package dependency**
   (same class as the LibreOffice note in the LiteParse backend): users
   install it themselves (`mise use pandoc@3` or their package manager).
   ParseCraft never downloads or bundles it — ADR-0001 §8 forbids committed
   binaries and network installs at runtime.
3. **Installing the extra and the binary is the user's licence decision.**
   GPL-2.0-or-later terms apply to that combination of extra + binary, not
   to the MIT-licensed core. Missing extra or missing binary each surface as
   a typed `DependencyUnavailableError` naming what to install.
4. **`supported_formats` may only list inputs verified against a real pandoc
   binary** (`pandoc --version` plus an actual round-trip conversion); the
   verified list lives with the backend and is not guessed from pandoc's
   documentation.

## Consequences

- The `pandoc` extra is copyleft-adjacent and never enters the default or
  `all` convenience profiles without this addendum (it ships as an extra
  entry only, like `pdf`).
- `uv sync --all-extras --all-groups` (root AGENTS.md rule 10) must keep
  resolving: pypandoc is a small MIT wrapper with no native pins, so the
  extra adds no python-level conflicts; platform/system dependencies stay
  outside the resolver by construction.
- Any future copyleft dependency follows the same pattern: optional extra +
  addendum + no core/dev install + typed errors when absent.

## Amendment (2026-10-13): the `pandoc` extra is gone — the engine gate replaces it

**Decision 1 is reversed, deliberately.** `pandoc` was an optional extra because the
Pandoc binary is GPL-2.0-or-later. That framing did not survive contact with what the
extra actually contained: the extra gated `pypandoc` — an MIT, pure-Python wrapper of
about 0 MB — while the copyleft binary it warned about is **not** a pip dependency at
all (decision 2) and remains the user's own system install. An extra that installs a
permissive wrapper while the licence decision it advertises lives outside the resolver
gates nothing the decision is about, so it was complexity, not a licence boundary.

**What replaces it.** `pandoc` is now a base-install backend with an **engine gate**
(`required_engine="pandoc"`, ADR-0008 decision 13): it is eligible only where the
binary is present — found on `PATH` or declared through the engine's environment
variable — which is a stronger guarantee than the extra ever gave, because the extra
never checked for the binary at all.

**The licence position is unchanged.** Subprocess invocation does not propagate
GPL-2.0-or-later to parsecraft: the wrapper is MIT, the binary is a separate program
the operator installs and licenses themselves, and parsecraft ships neither the binary
nor a copy of it. Decision 2 stands as written.

**Rule 10.** Removing the extra and adding `pypandoc` to the base dependencies keeps
`uv sync -U --all-extras --all-groups --all-packages --dry-run` at exit 0: the package
adds no native pins, and the system binary stays outside the resolver by construction.
