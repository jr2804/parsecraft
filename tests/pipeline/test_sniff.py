"""Tests for the unknown-suffix text gate (pc-9p6.14)."""

import codecs

from parsecraft.pipeline.analysis import sniff_text_plain


def test_plain_utf8_sniffs_as_text() -> None:
    assert sniff_text_plain(b"hello world, this is plain text\n" * 10) is True


def test_json_and_extensionless_bodies_sniff_as_text() -> None:
    assert sniff_text_plain(b'{"kind": "page", "n": 3}') is True
    assert sniff_text_plain(b"<note><to>user</to></note>") is True
    assert sniff_text_plain(b"no suffix, just words " * 20) is True


def test_realistic_legacy_encodings_sniff_as_text() -> None:
    latin1 = ("Café naïve façade résumé\n" * 40).encode("latin-1")
    assert sniff_text_plain(latin1) is True
    cp1252 = ("Résumé — €42 — naïve\n" * 40).encode("cp1252")
    assert sniff_text_plain(cp1252) is True
    utf16 = ("Hello world, this is a longer line of text.\n" * 20).encode("utf-16-le")
    assert sniff_text_plain(utf16) is True


def test_gbk_control_sniffs_as_text() -> None:
    assert sniff_text_plain(("中文测试混合文本\n" * 2000).encode("gbk")) is True


def test_utf16le_ascii_control_sniffs_as_text() -> None:
    assert sniff_text_plain(("Hello world test content. " * 8000).encode("utf-16-le")) is True


def test_tiny_utf16_bom_rescued_deterministically() -> None:
    assert sniff_text_plain("z".encode("utf-16")) is True


def test_empty_sniffs_as_text() -> None:
    assert sniff_text_plain(b"") is True


def test_binary_signatures_rejected() -> None:
    assert sniff_text_plain(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + bytes(2000)) is False
    assert sniff_text_plain(bytes.fromhex("89504e470d0a1a0a") + bytes(100)) is False
    assert sniff_text_plain(bytes(8192)) is False


def test_nul_interleaved_ascii_sniffs_as_text() -> None:
    """UTF-16-shaped bytes pass the sniff; the decode stage rules on them (warning)."""
    assert sniff_text_plain(b"a\x00b\x00c\x00" * 500) is True


def test_cjk_and_emoji_survive_window_slicing() -> None:
    """pc-3 adversarial: torn 32 KiB cuts must not flip multibyte UTF-8 (252 KB/115 KB shapes)."""
    cjk = ("中文测试混合文本内容，包含标点符号。" * 8000).encode("utf-8")
    assert len(cjk) > 65536
    assert sniff_text_plain(cjk) is True
    emoji = ("Hello \U0001f600 world test. " * 5000).encode("utf-8")
    assert len(emoji) > 65536
    assert sniff_text_plain(emoji) is True


def test_utf8_bom_prefix_declares_text() -> None:
    assert sniff_text_plain("Héllo\n".encode("utf-8-sig") * 10) is True


def test_malformed_utf8_bom_falls_through_to_windows() -> None:
    """A UTF-8 BOM prefix whose body is not strict UTF-8 is not a declaration (pc-9p6.14)."""
    malformed = codecs.BOM_UTF8 + b"\xff\xfe\x00binary-\x01\x02" * 40
    assert sniff_text_plain(malformed) is False


def test_truncated_utf16_prefix_defers_to_windows() -> None:
    """Odd-length 16-bit content is not a well-formed stream: no BOM fast path."""
    odd = "AB".encode("utf-16-le") + b"\x41"
    # binaryornot reads these NUL-light bytes as text — the windows decide, not the BOM.
    assert sniff_text_plain(odd) is True


def test_odd_length_bom_prefix_is_not_a_declaration() -> None:
    """A BOM prefix with a truncated final unit falls through to the windows (pc-9p6.14)."""
    odd = codecs.BOM_UTF16_LE + b"\x41\x00\x42"  # 5 bytes: (5 - 2) % 2 != 0
    assert sniff_text_plain(odd) is True


def test_mismatched_bom_falls_through_to_windows() -> None:
    """A UTF-32 BOM on UTF-16 content cannot decode strictly; windows judge the bytes."""
    mismatched = b"\xff\xfe\x00\x00" + "Mismatched BOM document text here.".encode("utf-16-le")
    assert sniff_text_plain(mismatched) is True


def test_surrogate_bearing_prefix_not_declared() -> None:
    """A lone surrogate fails strict validity; windows judge the bytes."""
    assert sniff_text_plain(b"\xff\xfeA\x00\x00\xd8B\x00") is True


def test_lone_be_bom_is_empty_text() -> None:
    assert sniff_text_plain(b"\xfe\xff") is True


def test_mid_buried_binary_is_a_documented_limit() -> None:
    """Binary ENTIRELY inside the unsampled middle is NOT detected (documented, not a gate)."""
    text = b"plain ascii line number %d\n" * 2000  # ~54 KB of ASCII lines
    blob = text[:40000] + bytes(40000) + text[:40000]  # zeros span [40000, 80000)
    assert len(blob) > 65536
    # Head [0, 32768) and tail [87232, 120000) are pure text; the zeros sit
    # fully inside the unsampled middle [32768, 87232) — detection is by design
    # bounded, not exhaustive (pc-2 seq 83: limitation, not a gate).
    assert sniff_text_plain(blob) is True


def test_bomless_utf16_cjk_is_a_documented_false_negative() -> None:
    """BOM-less UTF-16 CJK reads binary even full-scan (documented, not fixed)."""
    blob = ("中文测试混合文本内容，包含标点符号。" * 2500).encode("utf-16-le")
    assert sniff_text_plain(blob) is False
