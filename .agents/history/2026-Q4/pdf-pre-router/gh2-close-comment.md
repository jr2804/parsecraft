# gh-2 closure comment (paste-ready; gh token is EMU-write-blocked, so post manually)

Implemented in commits 7baa950 + d96febe (local main, push pending by maintainer).

- Entry-point registration (per root AGENTS.md rule 5 — not the issue's try/except pattern): light factory module + heavy `_impl` loaded via importlib at instantiation; extra `pdf-inspector>=1.25.2` (released version; the issue's 0.2.6 was wrong).
- DEVIATION (signed off): `convert()` is backed by `extract_pages_markdown_bytes`, not `process_pdf` — the latter returns ONE unattributeable whole-document Markdown blob (probed against the released wheel), so per-page IR pages and `page_range` would be fabricated. Same extraction path, real per-page results. Upstream page indexing is inconsistent (PageMarkdown.page 0-indexed, process_pdf pages= 1-indexed); normalization to 1-based IR happens in one place. Out-of-range ranges are typed INVALID_INPUT (upstream answers them with phantom empty pages — guarded).
- `analyze()` maps per-page Markdown onto PageSignal (no fabricated text stats from classify_pdf). supported_formats: application/pdf only. No OCR entry points ever called; model_asset=None. Existing typed error contracts only.
- Tests offline (heavy extension stubbed via sys.modules), 100% coverage gate. Live smoke vs released wheel: entry-point discovery clean, analyze/convert/pipeline dispatch verified.
- Full gate: `mise all` exit 0 — 870 passed, 17 skipped, 100% coverage, typecheck, docs.
- Contracts: src/parsecraft/backends/pdf_inspector/AGENTS.md (+ backends/AGENTS.md Child DOX Index), docs/reference/backends.md row.

The classify_pdf pre-router (#3) deliberately does NOT consume this backend's surface — see ADR-0004 amendment (068d10e) and beads pc-rzm/pc-1ow.
