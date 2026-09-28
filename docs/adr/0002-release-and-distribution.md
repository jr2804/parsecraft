# ADR-0002: Release & distribution — automatic CalVer, GitHub Pages, PyPI trusted publishing

- **Status:** Accepted
- **Date:** 2026-09-27
- **Deciders:** pc-1, user
- **Related:** ADR-0001 §1 (name/distribution), §8 (fixtures)

## Context

ADR-0001 fixed the name `parsecraft` and deferred public release gates. The
project needs an automated, low-touch path from "push to main" to a distributable
artifact, a published docs site, and a PyPI release — without hand-bumped
versions or stored publishing secrets.

## Decisions

### 1. Repository is public; docs publish to GitHub Pages

`github.com/jr2804/parsecraft` is **public**. GitHub Pages is enabled with
`build_type: workflow` → <https://jr2804.github.io/parsecraft/>. GitHub Pages is
not available for private repositories on the free plan (verified: HTTP 422 from
the Pages API), so public visibility is a hard prerequisite, not a preference.

Zensical's `site_url` is the project-page URL (`https://jr2804.github.io/parsecraft/`),
distinct from `repo_url`.

### 2. Automatic CalVer on every push to `main`

- Tag format: `YYYY.M.N` — **no `v` prefix**, month is **not zero-padded**
  (matches PyPI's normalized form), `N` increments within the month.
- A tag is created only for a commit that has none; a re-run of an already-tagged
  commit reuses the tag (`needs_tag=false`) so it is idempotent.
- Git tag text matches the wheel version exactly — no PEP 440 padding skew.
- `uv-dynamic-versioning` (dunamai) derives the package version from the tag;
  `fallback-version = "0.0.0"`. dunamai's default tag pattern expects a `v`
  prefix, so `[tool.uv-dynamic-versioning] pattern` is set explicitly to the
  CalVer form `^(?P<base>\d+\.\d+\.\d+)$`. Without it, bare tags are ignored
  and the build falls back to a local version (`0.0.0.postN.dev0+<hash>`) that
  PyPI rejects (HTTP 400).

~~Every push to `main` produces a release, including docs-only pushes.~~
**Amended 2026-09-28:** every push to `main` is validated by CI, but a release is
a **deliberate milestone**. A push whose head commit message contains
`[skip release]` is never tagged, published, or deployed; omit the marker to cut
a release. Development pushes carry the marker; milestone pushes omit it.
(Originally every push released — abandoned after 25 releases accumulated during
an unfinished development phase.)

### 3. PyPI publishing uses trusted publishing (OIDC)

No long-lived PyPI token is stored. The release workflow requests `id-token: write`
in a `pypi` environment; `pypa/gh-action-pypi-publish` exchanges it for a
short-lived credential. Requires a one-time pending-publisher registration on
PyPI: owner `jr2804`, repository `parsecraft`, workflow `release.yml`,
environment `pypi`.

`skip-existing: true` keeps re-runs safe when a version already exists.

### 4. One workflow owns the whole release

`.github/workflows/release.yml` performs, in order: compute CalVer → tag → build →
GitHub release → PyPI publish → GitHub Pages deploy. The separate `docs.yml` was
removed to avoid two competing Pages deployments. `ci.yml` remains the
quality/test gate on pull requests.

## Consequences

- First publish reserves the PyPI name (ADR-0001 §1 consequence).
- Pages availability is coupled to repository visibility; a future move back to
  private would break the docs deploy.
- CalVer tags are the sole source of the package version — no version file to bump.
