#!/usr/bin/env python3
r"""On-demand docling-vs-native-pdf benchmark with per-pair resume.

This is **not** a pytest test and is never collected by ``mise test``/CI: it
loads the real docling pipeline and can take minutes to hours per document. It
measures one ``(document, backend)`` pair at a time, persists each result to a
partial file immediately, and skips pairs already measured — so a long run is
resumable instead of restartable.

Measurement never uses ``parsecraft``'s ``ConversionCache``: a cache hit would
report ~0 s and falsify the benchmark. Document bytes are read locally and never
fetched.

Usage::

    uv run --extra docling --extra pdf --extra pdf-lite \
        python scripts/bench_docling.py .tmp/docling-bench/*.pdf
"""

from __future__ import annotations

import argparse
import json
import sys
from importlib.metadata import entry_points
from pathlib import Path
from time import monotonic
from typing import cast

from parsecraft.backends.registry import BackendRegistry
from parsecraft.benchmark import BenchmarkReport, run_benchmark, write_json
from parsecraft.routing import RoutingConstraints

#: Backends compared head-to-head (docling first: it is the slow one).
DEFAULT_BACKENDS: tuple[str, ...] = ("docling", "native-pdf")
DEFAULT_PARTIAL = Path(".tmp/bench-docling-partial.json")
DEFAULT_OUTPUT = Path("docs/benchmarks/benchmark-docling-vs-native.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("documents", nargs="+", type=Path, help="Local documents to benchmark")
    parser.add_argument("--partial", type=Path, default=DEFAULT_PARTIAL, help="Resume file (written after every pair)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Committed report written when the run completes")
    parser.add_argument("--backends", nargs="+", default=list(DEFAULT_BACKENDS), help="Backends to compare")
    args = parser.parse_args(argv)

    documents = sorted(set(args.documents))
    partial = _load_partial(args.partial)
    rows = _rows(partial)
    skips = cast("list[dict[str, object]]", partial.get("skips", []))
    done = {_pair_key(str(row["document"]), str(row["backend"])) for row in rows}
    package_version = str(partial.get("package_version", "0.0.0"))
    constraints = _constraints()
    total = len(documents) * len(args.backends)
    index = 0

    for document in documents:
        for backend in args.backends:
            index += 1
            key = _pair_key(document.name, backend)
            if key in done:
                print(f"[{index}/{total}] skip {document.name} {backend} (already measured)", flush=True)
                continue
            started = monotonic()
            report = run_benchmark([document], _registry(backend), constraints)
            wall_s = monotonic() - started
            payload = report.model_dump(mode="json")
            package_version = report.package_version
            rows.extend(cast("list[dict[str, object]]", payload.get("results", [])))
            skips.extend(cast("list[dict[str, object]]", payload.get("skips", [])))
            rows = _dedupe(rows)
            done.add(key)
            partial = cast("dict[str, object]", {"package_version": package_version, "results": rows, "skips": skips})
            _write_partial(args.partial, partial)
            where = f"wall {wall_s:.1f} s"
            if report.results:
                measured = report.results[0]
                where = f"{measured.elapsed_s:.3f} s, {measured.pages} pages; {where}"
            print(f"[{index}/{total}] {document.name} {backend}: {where} -> {args.partial}", flush=True)

    benchmark_report = BenchmarkReport.model_validate(
        {
            "package_version": package_version,
            "results": sorted(_dedupe(rows), key=lambda row: (str(row["document"]), str(row["backend"]))),
            "skips": sorted(skips, key=lambda skip: (str(skip["document"]), str(skip["reason"]))),
        }
    )
    write_json(benchmark_report, args.output)
    print(f"wrote {args.output} ({len(rows)} rows, {len(skips)} skips)", flush=True)
    return 0


def _constraints() -> RoutingConstraints:
    """Explicit constraints: the probe cannot see the heavy extras by design."""
    return RoutingConstraints(
        installed_extras={"pdf-lite", "pdf", "docling"},
        vram_budget_gb=0.0,
        offline=False,
        allow_ocr=False,
    )


def _registry(name: str) -> BackendRegistry:
    """A registry holding exactly one backend, loaded from its entry point."""
    factories = {entry.name: entry for entry in entry_points(group="parsecraft.backends")}
    if name not in factories:
        msg = f"backend {name!r} is not registered; install its extra first"
        raise SystemExit(msg)
    registry = BackendRegistry()
    registry.register(name, factories[name].load())
    registry._entry_points_loaded = True  # isolate: measure exactly this backend  # noqa: SLF001
    return registry


def _pair_key(document: str, backend: str) -> str:
    return f"{Path(document).name}|{backend}"


def _load_partial(path: Path) -> dict[str, object]:
    """Plain-JSON partial payload (``results``/``skips`` lists), or an empty one."""
    if not path.is_file():
        return {"package_version": "0.0.0", "results": [], "skips": []}
    return cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))


def _write_partial(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


def _rows(payload: dict[str, object]) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", payload.get("results", []))


def _dedupe(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """One row per (document, backend), last measurement wins (resume safety)."""
    by_key: dict[tuple[str, str], dict[str, object]] = {}
    for row in rows:
        by_key[(str(row["document"]), str(row["backend"]))] = row
    return list(by_key.values())


if __name__ == "__main__":
    sys.exit(main())
