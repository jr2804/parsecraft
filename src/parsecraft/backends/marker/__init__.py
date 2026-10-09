"""Marker backend family — high-accuracy PDF→Markdown (Apache-2.0 / OpenRAIL-M).

Entry point: ``parsecraft.backends.marker.marker:factory`` (light, imports no
``marker`` code); the heavy ``_impl`` module is pulled in at instantiation via
``importlib`` — never an inline import (pyreorder hoists those).

**Licence caveat (ADR-0006):** the ``marker-pdf`` wrapper is Apache-2.0, but
its default OCR pipeline pulls Surya weights under a modified **OpenRAIL-M**
licence (research/personal/startup-under-cap only). The adapter code here is
MIT; the restricted-weights rule means marker-pdf is a **bring-your-own
dependency**: parsecraft declares no ``marker`` extra, the consumer's own
dependency graph installs it, and the backend is routable exactly where the
import resolves.

**Construction-time network:** ``create_model_dict()`` fetches model weights
(14 MB font on first run). Per ADR-0006 the font fetch must be pre-seeded or
deferred — a failure is recorded as a typed ``DEPENDENCY_MISSING`` pass-failure
instead of crashing or mutating site-packages behind the operator's back.

**Supported formats:** ``application/pdf`` only, per ADR-0006 decision 3 —
DOCX/XLSX/PPTX/HTML/EPUB are upstream-marketed but unverified on the canonical
platform (Windows) and route through weasyprint (GTK/Pango system dependency).
Re-entry: re-run the recon when upstream pins allow joint resolution.
"""
