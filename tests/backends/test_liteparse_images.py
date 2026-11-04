"""LiteParse figure surface: extracted images → IR assets and figure chunks (pc-usi).

Two layers are covered here. The pure mapping (``_images``) is tested offline,
against hand-made records. The seam is tested against the REAL engine: the
adapter once shipped a figure surface that read a liteparse record the way it
was not shaped (issue #7 was the same class of bug), so the handoff is
exercised with liteparse's own dataclasses and with a real parse of a real
PDF — if liteparse changes the record, this fails here instead of inside a
consumer's conversion.
"""

from __future__ import annotations

import hashlib
import importlib
import sys
from typing import cast

import pytest

from parsecraft.backends.liteparse import liteparse as liteparse_module
from parsecraft.backends.liteparse._images import (
    UNKNOWN_MEDIA_TYPE,
    ImageRectLike,
    bbox_of,
    figure_chunk,
    figures_for_page,
    media_type,
)
from parsecraft.backends.protocol import BackendConfig, ConversionRequest, DocumentBackend, SourceDocument
from parsecraft.ir.models import AssetKind, ChunkKind
from tests.fixtures.documents import figure_pdf

_FIGURE_BYTES = figure_pdf()
_FIGURE_SOURCE = SourceDocument(uri="file:///figure.pdf", content=_FIGURE_BYTES)


class _Rect:
    """Stand-in for liteparse's ``ImageRect`` (viewport coordinates)."""

    def __init__(self, x: float, y: float, width: float, height: float) -> None:
        self.x = x
        self.y = y
        self.width = width
        self.height = height


class _Image:
    """Stand-in for liteparse's ``ExtractedImage``."""

    def __init__(
        self,
        *,
        id: str = "p1_1",
        page: int = 1,
        format: str = "png",
        payload: bytes = b"\x89PNG\r\n\x1a\npayload",
        bbox: ImageRectLike | None = None,
    ) -> None:
        self.id = id
        self.page = page
        self.format = format
        self.bytes = payload
        self.bbox: ImageRectLike = _Rect(72.0, 192.0, 200.0, 200.0) if bbox is None else bbox


# ── The pure mapping ─────────────────────────────────────────────────────────────


def test_media_type_comes_from_the_format_the_engine_reports() -> None:
    assert media_type(_Image(format="png")) == "image/png"
    assert media_type(_Image(format="JPG")) == "image/jpeg"
    assert media_type(_Image(format=" jpeg ")) == "image/jpeg"
    assert media_type(_Image(format="tiff")) == "image/tiff"


def test_an_unnamed_format_is_sniffed_from_the_payload() -> None:
    """A format the table does not know still yields the truth about the bytes."""
    assert media_type(_Image(format="", payload=b"\x89PNG\r\n\x1a\nrest")) == "image/png"
    assert media_type(_Image(format="x", payload=b"\xff\xd8\xff\xe0rest")) == "image/jpeg"
    assert media_type(_Image(format="x", payload=b"GIF89arest")) == "image/gif"
    assert media_type(_Image(format="x", payload=b"RIFF\x00\x00\x00\x00WEBPrest")) == "image/webp"


def test_an_unrecognisable_payload_is_declared_opaque_rather_than_mislabelled() -> None:
    assert media_type(_Image(format="x", payload=b"not an image")) == UNKNOWN_MEDIA_TYPE


def test_the_bbox_is_the_viewport_rect_as_is() -> None:
    """LiteParse reports the top-left viewport frame; the IR wants that frame."""
    bbox = bbox_of(_Image())
    assert bbox is not None
    assert (bbox.x0, bbox.y0, bbox.x1, bbox.y1) == (72.0, 192.0, 272.0, 392.0)


def test_a_malformed_box_vanishes_instead_of_failing_the_page() -> None:
    """A broken placement costs the box, never the image."""
    assert bbox_of(_Image(bbox=cast(ImageRectLike, object()))) is None
    assert bbox_of(_Image(bbox=_Rect(float("nan"), 0.0, 10.0, 10.0))) is None


def test_each_image_becomes_one_asset_with_a_digest_and_a_data_uri() -> None:
    payload = b"\x89PNG\r\n\x1a\npixels"
    figures = figures_for_page([_Image(payload=payload)], page_number=3)
    assert len(figures) == 1
    asset = figures[0].asset
    assert asset.id == "liteparse-p3-a0"
    assert asset.kind is AssetKind.IMAGE
    assert asset.media_type == "image/png"
    assert asset.sha256 == hashlib.sha256(payload).hexdigest()
    assert asset.uri.startswith("data:image/png;base64,")
    assert asset.bbox is not None


def test_an_image_without_payload_bytes_is_skipped() -> None:
    """An asset with no payload would digest nothing; a chunk without an asset would dangle."""
    assert figures_for_page([_Image(payload=b"")], page_number=1) == []


def test_a_figure_chunk_links_to_its_asset_and_carries_no_caption() -> None:
    """LiteParse supplies no caption, so the content is empty — the chunk still exists."""
    figures = figures_for_page([_Image()], page_number=1)
    chunk = figure_chunk(figures[0], page_number=1, index=2)
    assert chunk.kind is ChunkKind.FIGURE
    assert chunk.content == ""
    assert chunk.metadata["asset_id"] == figures[0].asset.id
    assert chunk.metadata["backend"] == "liteparse"
    assert chunk.reading_order == 2
    assert chunk.id == "liteparse-p1-b2"


# ── The seam: liteparse's own records ───────────────────────────────────────────


def test_the_real_extracted_image_record_maps_onto_the_ir() -> None:
    """The engine's own dataclass, not a hand-made stand-in.

    The adapter once read liteparse's records the way they were not shaped
    (issue #7). Building the record with liteparse's own types makes an
    upstream change fail here instead of in a consumer's conversion.
    """
    types = pytest.importorskip("liteparse.types")

    record = types.ExtractedImage(
        id="p5_1",
        name="img_p5_1.png",
        path=None,
        page=5,
        bbox=types.ImageRect(x=105.0, y=391.0, width=360.0, height=324.0),
        width=750,
        height=676,
        rotation=0.0,
        format="png",
        bytes=b"\x89PNG\r\n\x1a\nreal",
        duplicate_of=None,
    )
    figures = figures_for_page([record], page_number=5)
    asset = figures[0].asset
    assert asset.id == "liteparse-p5-a0"
    assert asset.media_type == "image/png"
    assert asset.sha256 == hashlib.sha256(b"\x89PNG\r\n\x1a\nreal").hexdigest()
    assert asset.bbox is not None
    assert (asset.bbox.x0, asset.bbox.y0, asset.bbox.x1, asset.bbox.y1) == (105.0, 391.0, 465.0, 715.0)


def test_the_real_engine_reports_the_drawn_figure_as_an_asset_and_a_chunk() -> None:
    backend = _real_backend({"extract_images": "true"})
    result = backend.convert(ConversionRequest(source=_FIGURE_SOURCE))
    assert result.failures == []
    page = result.pages[0]
    assert len(page.assets) == 1
    asset = page.assets[0]
    assert asset.kind is AssetKind.IMAGE
    assert asset.media_type == "image/png"
    assert asset.sha256 is not None
    assert asset.uri.startswith("data:image/png;base64,")
    # The drawn placement, in the viewport frame: 72,192 over 200x200 points.
    assert asset.bbox is not None
    assert (asset.bbox.x0, asset.bbox.y0) == (72.0, 192.0)
    figures = [block for block in page.blocks if block.kind is ChunkKind.FIGURE]
    assert len(figures) == 1
    assert figures[0].metadata["asset_id"] == asset.id
    assert figures[0].content == ""  # no caption from the engine


def test_the_figure_lands_between_the_text_above_and_below_it() -> None:
    """Reading order by the figure's top edge against the text baselines."""
    backend = _real_backend({"extract_images": "true"})
    page = backend.convert(ConversionRequest(source=_FIGURE_SOURCE)).pages[0]
    kinds = [(block.kind, block.content) for block in page.blocks]
    assert kinds[0][0] is ChunkKind.PARAGRAPH
    assert kinds[0][1] == "Figure fixture Second body line."
    assert kinds[1][0] is ChunkKind.FIGURE
    assert kinds[2][0] is ChunkKind.PARAGRAPH
    assert kinds[2][1] == "Caption below the figure."
    assert [block.reading_order for block in page.blocks] == [0, 1, 2]


def test_without_the_option_the_output_is_unchanged() -> None:
    """``extract_images`` defaults to off: no asset, no figure, same blocks."""
    backend = _real_backend()
    page = backend.convert(ConversionRequest(source=_FIGURE_SOURCE)).pages[0]
    assert page.assets == []
    assert [block.kind for block in page.blocks] == [ChunkKind.PARAGRAPH, ChunkKind.PARAGRAPH]
    assert [block.content for block in page.blocks] == ["Figure fixture Second body line.", "Caption below the figure."]


# ── The seam: a real parse of a real PDF ────────────────────────────────────────


def _real_backend(options: dict[str, str] | None = None) -> DocumentBackend:
    """A backend on the REAL engine (the impl is re-imported, so no stub leaks in)."""
    pytest.importorskip("liteparse")
    sys.modules.pop("parsecraft.backends.liteparse._impl", None)
    return liteparse_module.factory(BackendConfig(name="liteparse", options=options or {}))


def test_the_impl_module_is_reimportable_against_the_real_engine() -> None:
    """The offline stub in the sibling module must not leak into a real run."""
    pytest.importorskip("liteparse")
    sys.modules.pop("parsecraft.backends.liteparse._impl", None)
    impl = importlib.import_module("parsecraft.backends.liteparse._impl")
    assert impl.LiteParse.__module__.startswith("liteparse.")
