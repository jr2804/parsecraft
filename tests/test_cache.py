"""Offline tests for the conversion cache: keys, hits/misses, CLI surface."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from parsecraft.backends.native.text import TextBackend
from parsecraft.cache import ConversionCache, default_cache_root
from parsecraft.cli.app import app
from parsecraft.ir.models import (
    ChunkKind,
    DocumentMetadata,
    DocumentResult,
    PageResult,
    StructuredChunk,
)

PRODUCED = "2026-09-28T00:00:00+00:00"
KEY_A = "a" * 64
KEY_B = "b" * 64

_runner = CliRunner()


def envelope(key: str, result: object, schema: int = 1) -> str:
    return json.dumps({"schema_version": schema, "key": key, "result": result}) + "\n"


# ── location ───────────────────────────────────────────────────────────────


def test_default_root_honours_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSECRAFT_CACHE_DIR", str(tmp_path / "override"))
    assert default_cache_root() == tmp_path / "override"
    assert ConversionCache().root == tmp_path / "override"


def test_default_root_falls_back_to_user_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PARSECRAFT_CACHE_DIR", raising=False)
    assert default_cache_root().name == "conversions"
    assert "parsecraft" in str(default_cache_root())


# ── round trip + atomicity ─────────────────────────────────────────────────


def test_put_get_round_trip_is_byte_identical(tmp_path: Path) -> None:
    store = ConversionCache(root=tmp_path)
    document = make_document()
    store.put(KEY_A, document)
    first_bytes = (tmp_path / f"{KEY_A}.json").read_bytes()
    assert store.get(KEY_A) == document
    # re-put of the same value reproduces the same envelope bytes
    store.put(KEY_A, document)
    assert (tmp_path / f"{KEY_A}.json").read_bytes() == first_bytes
    reloaded = store.get(KEY_A)
    assert reloaded is not None
    assert reloaded.model_dump(mode="json") == document.model_dump(mode="json")


def test_put_leaves_no_temp_files(tmp_path: Path) -> None:
    store = ConversionCache(root=tmp_path)
    store.put(KEY_A, make_document())
    assert sorted(path.name for path in tmp_path.iterdir()) == [f"{KEY_A}.json"]


def test_put_overwrites_existing_key(tmp_path: Path) -> None:
    store = ConversionCache(root=tmp_path)
    store.put(KEY_A, make_document("first"))
    store.put(KEY_A, make_document("second"))
    assert store.get(KEY_A) == make_document("second")


def test_put_rejects_invalid_key(tmp_path: Path) -> None:
    store = ConversionCache(root=tmp_path)
    with pytest.raises(ValueError, match="invalid cache key"):
        store.put("../../etc/passwd", make_document())


# ── miss conditions (total reads) ──────────────────────────────────────────


def test_missing_entry_is_a_miss(tmp_path: Path) -> None:
    assert ConversionCache(root=tmp_path).get(KEY_A) is None


def test_invalid_key_is_a_miss(tmp_path: Path) -> None:
    store = ConversionCache(root=tmp_path)
    assert store.get("not-a-key") is None
    assert store.get(f"{KEY_A}/../../x") is None


@pytest.mark.parametrize(
    "payload",
    [
        "not json {{{\n",
        "[1, 2, 3]\n",
        '{"schema_version": 999, "key": "' + "a" * 64 + '", "result": {}}\n',
        '{"schema_version": 1, "key": "' + "b" * 64 + '", "result": {}}\n',
        '{"schema_version": 1, "key": "' + "a" * 64 + '", "result": {"nonsense": 1}}\n',
        '{"schema_version": 1, "key": "' + "a" * 64 + '", "result": "not-a-dict"}\n',
    ],
)
def test_malformed_entries_are_misses(tmp_path: Path, payload: str) -> None:
    (tmp_path / f"{KEY_A}.json").write_text(payload, encoding="utf-8")
    assert ConversionCache(root=tmp_path).get(KEY_A) is None


def test_truncated_entry_is_a_miss(tmp_path: Path) -> None:
    store = ConversionCache(root=tmp_path)
    store.put(KEY_A, make_document())
    raw = (tmp_path / f"{KEY_A}.json").read_text(encoding="utf-8")
    (tmp_path / f"{KEY_A}.json").write_text(raw[: len(raw) // 2], encoding="utf-8")
    assert store.get(KEY_A) is None


def test_invalid_utf8_entry_is_a_miss(tmp_path: Path) -> None:
    (tmp_path / f"{KEY_A}.json").write_bytes(b"\xff\xfe\x00 not utf-8")
    assert ConversionCache(root=tmp_path).get(KEY_A) is None


# ── inspect / clear / sweep ────────────────────────────────────────────────


def test_entries_sorted_and_ignore_junk(tmp_path: Path) -> None:
    store = ConversionCache(root=tmp_path)
    store.put(KEY_B, make_document("b"))
    store.put(KEY_A, make_document("a"))
    (tmp_path / "notes.txt").write_text("stray", encoding="utf-8")
    (tmp_path / ".leftover.tmp").write_text("partial", encoding="utf-8")
    entries = store.entries()
    assert [entry.key for entry in entries] == [KEY_A, KEY_B]
    assert store.total_bytes() == sum(entry.size_bytes for entry in entries)


def test_entries_on_missing_root(tmp_path: Path) -> None:
    store = ConversionCache(root=tmp_path / "absent")
    assert store.entries() == []
    assert store.total_bytes() == 0


def test_clear_removes_all_entries(tmp_path: Path) -> None:
    store = ConversionCache(root=tmp_path)
    store.put(KEY_A, make_document())
    store.put(KEY_B, make_document())
    assert store.clear() == 2
    assert store.entries() == []
    assert store.clear() == 0


def test_sweep_deletes_oldest_first(tmp_path: Path) -> None:
    store = ConversionCache(root=tmp_path)
    store.put(KEY_A, make_document("old"))
    store.put(KEY_B, make_document("new"))
    os.utime(tmp_path / f"{KEY_A}.json", (1000, 1000))  # clearly older
    os.utime(tmp_path / f"{KEY_B}.json", (2000, 2000))
    newest_size = (tmp_path / f"{KEY_B}.json").stat().st_size  # cap fits one entry only
    assert store.sweep(newest_size) == 1
    assert [entry.key for entry in store.entries()] == [KEY_B]
    assert store.sweep(0) == 1
    assert store.entries() == []


# ── CLI surface ────────────────────────────────────────────────────────────


def test_cache_command_shows_and_clears(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSECRAFT_CACHE_DIR", str(tmp_path))
    ConversionCache(root=tmp_path).put(KEY_A, make_document())

    shown = _runner.invoke(app, ["cache"])
    assert shown.exit_code == 0
    assert "entries: 1" in shown.output
    assert str(tmp_path) in shown.output

    as_json = _runner.invoke(app, ["cache", "--json"])
    assert as_json.exit_code == 0
    payload = json.loads(as_json.output)
    assert payload["entries"] == 1
    assert payload["keys"] == [KEY_A]
    assert list(payload) == sorted(payload)

    cleared = _runner.invoke(app, ["cache", "--clear"])
    assert cleared.exit_code == 0
    assert "removed 1 conversion cache entries" in cleared.output
    assert ConversionCache(root=tmp_path).entries() == []


def test_cache_command_on_empty_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSECRAFT_CACHE_DIR", str(tmp_path))
    result = _runner.invoke(app, ["cache"])
    assert result.exit_code == 0
    assert "entries: 0" in result.output


def test_convert_cache_flag_hits_even_when_dispatch_would_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSECRAFT_CACHE_DIR", str(tmp_path / "conversions"))
    source = _write_source(tmp_path)

    # Run 1: real backends, --cache populates the store.
    first = _runner.invoke(app, ["convert", str(source), "--cache"])
    assert first.exit_code == 0, first.output
    assert ConversionCache(root=tmp_path / "conversions").entries()

    def refuse(self: object, request: object) -> object:
        raise AssertionError("cache hit must not dispatch convert()")

    monkeypatch.setattr(TextBackend, "convert", refuse)

    # …run 2 succeeds on the stored IR without dispatching…
    second = _runner.invoke(app, ["convert", str(source), "--cache"])
    assert second.exit_code == 0, second.output
    assert second.output == first.output  # identical stored IR renders identically

    # …while --no-cache dispatches and fails loudly.
    third = _runner.invoke(app, ["convert", str(source), "--no-cache"])
    assert third.exit_code != 0


# -- convert --cache: a hit must prove no dispatch ---------------------------


def _write_source(tmp_path: Path) -> Path:
    path = tmp_path / "doc.txt"
    path.write_text(
        "first paragraph with enough characters to route natively.\n\nsecond paragraph, also long enough.",
        encoding="utf-8",
    )
    return path


# ── resilience: vanishing files never break inspect/clear/sweep ────────────


def test_entries_skips_unreadable_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ConversionCache(root=tmp_path)
    store.put(KEY_A, make_document())
    (tmp_path / "not-a-key.json").write_text("{}", encoding="utf-8")  # invalid key → skipped
    (tmp_path / f"{'d' * 64}.json").mkdir()  # valid-key directory is not an entry

    real_stat = Path.stat

    def flaky(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == f"{KEY_A}.json":
            raise OSError("vanished")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky)
    # invalid-key file skipped, unreadable stat skipped → empty, no error
    assert store.entries() == []


def test_clear_continues_past_unlink_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ConversionCache(root=tmp_path)
    store.put(KEY_A, make_document())
    store.put(KEY_B, make_document())

    real_unlink = Path.unlink

    def flaky(self: Path, *args: Any, **kwargs: Any) -> None:
        if self.name == f"{KEY_A}.json":
            raise OSError("locked")
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky)
    assert store.clear() == 1  # A locked, B removed
    assert [entry.key for entry in store.entries()] == [KEY_A]


def test_sweep_skips_entries_it_cannot_touch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ConversionCache(root=tmp_path)
    store.put(KEY_A, make_document("old"))
    store.put(KEY_B, make_document("new"))
    os.utime(tmp_path / f"{KEY_A}.json", (1000, 1000))
    os.utime(tmp_path / f"{KEY_B}.json", (2000, 2000))

    real_unlink = Path.unlink
    real_getmtime = os.path.getmtime

    def mtime_flaky(path: Any) -> float:
        if Path(path).name == f"{KEY_B}.json":
            raise OSError("vanished mid-sweep")
        return real_getmtime(path)

    def unlink_flaky(self: Path, *args: Any, **kwargs: Any) -> None:
        if self.name == f"{KEY_A}.json":
            raise OSError("locked")
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr("parsecraft.cache.store.os.path.getmtime", mtime_flaky)
    monkeypatch.setattr(Path, "unlink", unlink_flaky)
    assert store.sweep(0) == 0  # A locked, B un-mtime-able — nothing removed, no error


def make_document(text: str = "cached content") -> DocumentResult:
    page = PageResult(
        page_number=1,
        blocks=[
            StructuredChunk(
                id="c1",
                kind=ChunkKind.PARAGRAPH,
                content=text,
                page_number=1,
                reading_order=0,
            )
        ],
    )
    metadata = DocumentMetadata(
        source_uri="mem://cached",
        source_hash="0" * 64,
        format="text/plain",
        page_count=1,
        title=None,
        produced_at=PRODUCED,  # pydantic parses the ISO literal
        package_version="1.2.3",
    )
    return DocumentResult(metadata=metadata, pages=[page], trace=[], quality=[])
