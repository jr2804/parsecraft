"""Shared file:// URI resolution (pc-4u7.36): one helper, Windows-aware."""

from __future__ import annotations

from pathlib import Path

from parsecraft.backends.source import path_from_file_uri


def test_drive_slash_form_is_unwrapped() -> None:
    assert path_from_file_uri("file:///C:/Users/x/doc.pdf") == Path("C:/Users/x/doc.pdf")


def test_drive_backslash_form_lands_in_netloc() -> None:
    # file://C:\\... has no URL separators: the whole local path sits in netloc.
    assert path_from_file_uri("file://C:/Users/jan/Temp/doc.pdf") == Path("C:/Users/jan/Temp/doc.pdf")


def test_unc_form_keeps_the_host() -> None:
    assert path_from_file_uri("file://server/share/report.pdf") == Path("//server/share/report.pdf")


def test_percent_encoding_is_decoded() -> None:
    assert path_from_file_uri("file://server/share/a%20b.pdf") == Path("//server/share/a b.pdf")


def test_posix_style_path_passes_through() -> None:
    result = path_from_file_uri("file:///home/u/d.pdf")
    assert result == Path("/home/u/d.pdf")


def test_localhost_is_not_a_host() -> None:
    assert path_from_file_uri("file://localhost/C:/y/z.pdf") == Path("C:/y/z.pdf")
