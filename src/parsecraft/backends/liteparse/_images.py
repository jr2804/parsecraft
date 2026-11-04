"""Figure surface for LiteParse: extracted images → typed IR assets and chunks.

LiteParse returns the embedded raster images of a document on the PARSE RESULT
(``result.images`` — one ``ExtractedImage`` per image), not on the page, and
only when the caller asked for them (``extract_images=True``). This module
maps them onto the IR's figure surface: one ``ExtractedAsset`` per image plus
one ``FIGURE`` chunk per asset, linked by ``metadata["asset_id"]`` — the shape
the ``docling`` and ``mineru`` backends already emit (pc-sc9).

The mapping is deliberately pure and liteparse-free: it takes anything with the
handful of attributes :class:`ImageLike` names, so it is unit-testable offline
against recorded image dumps and cannot import the engine by accident.

Frames: LiteParse reports an image's placement in **viewport coordinates** —
top-left origin, 72 DPI, one PDF point per unit — which is the IR's own raster
top-left frame. The mapping is therefore a plain offset with no flip (the
``docling`` backend has to flip, because docling's provenance frame is
BOTTOMLEFT). The same frame carries the text items' baselines, so a figure's
top edge is directly comparable with a text block's y.
"""

from __future__ import annotations

import base64
import hashlib
import math
from dataclasses import dataclass
from typing import Protocol

from parsecraft.ir.models import AssetKind, BBox, ChunkKind, ExtractedAsset, StructuredChunk

#: Media type per image format LiteParse reports.
_MEDIA_TYPES: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "webp": "image/webp",
    "svg": "image/svg+xml",
}

#: Payload signatures, for a format the engine did not name.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)

#: The media type of a payload that is neither named nor recognisable.
UNKNOWN_MEDIA_TYPE = "application/octet-stream"


@dataclass(slots=True)
class Figure:
    """One extracted image: the asset it becomes and where it sits on the page."""

    asset: ExtractedAsset
    #: The image's top edge in viewport points (top-left origin) — its place
    #: in the page's reading order.
    top: float


class ImageRectLike(Protocol):
    """The slice of LiteParse's ``ImageRect`` the mapping reads."""

    x: float
    y: float
    width: float
    height: float


class ImageLike(Protocol):
    """The slice of one LiteParse ``ExtractedImage`` the mapping reads."""

    id: str
    page: int
    format: str
    bytes: bytes
    bbox: ImageRectLike


def figures_for_page(images: list[ImageLike], *, page_number: int) -> list[Figure]:
    """One figure per extracted image, in the engine's order.

    An image with no payload bytes is skipped: an asset with an empty payload
    would carry a digest of nothing, and a figure chunk without its asset would
    dangle. The asset id counts the emitted assets, so the ids stay contiguous.
    """
    figures: list[Figure] = []
    for image in images:
        payload = bytes(image.bytes or b"")
        if not payload:
            continue
        media = media_type(image)
        bbox = bbox_of(image)
        asset = ExtractedAsset(
            id=f"liteparse-p{page_number}-a{len(figures)}",
            kind=AssetKind.IMAGE,
            media_type=media,
            uri=f"data:{media};base64," + base64.b64encode(payload).decode("ascii"),
            bbox=bbox,
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        figures.append(Figure(asset=asset, top=bbox.y0 if bbox is not None else 0.0))
    return figures


def media_type(image: ImageLike) -> str:
    """The image's media type: the engine's format name, else the payload's signature.

    The engine re-encodes what it extracts, so the format it reports and the
    bytes it returns agree; the signature is the fallback for a format the
    table does not know, and a payload that matches neither is declared as
    opaque bytes rather than mislabelled.
    """
    named = _MEDIA_TYPES.get(str(image.format or "").strip().lower())
    if named is not None:
        return named
    payload = bytes(image.bytes or b"")
    for signature, media in _SIGNATURES:
        if payload.startswith(signature):
            return media
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    return UNKNOWN_MEDIA_TYPE


def bbox_of(image: ImageLike) -> BBox | None:
    """The image's placement in the IR's raster top-left frame; malformed boxes vanish.

    LiteParse's ``ImageRect`` is an origin plus a size, so the IR's box is the
    origin and its far corner — no flip, no page height needed.
    """
    rect = image.bbox
    try:
        left = float(rect.x)
        top = float(rect.y)
        right = left + float(rect.width)
        bottom = top + float(rect.height)
    except (AttributeError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (left, top, right, bottom)):
        return None
    # min/max normalises a rect whose size is negative; the IR wants ordered corners.
    return BBox(x0=min(left, right), y0=min(top, bottom), x1=max(left, right), y1=max(top, bottom))


def figure_chunk(figure: Figure, *, page_number: int, index: int) -> StructuredChunk:
    """The FIGURE chunk that links to the asset.

    The content is the caption, and LiteParse supplies none for an extracted
    image — so it is empty. The chunk still exists: a consumer attaches the
    image by ``metadata["asset_id"]``, not by its text.
    """
    return StructuredChunk(
        id=f"liteparse-p{page_number}-b{index}",
        kind=ChunkKind.FIGURE,
        content="",
        page_number=page_number,
        reading_order=index,
        metadata={"backend": "liteparse", "asset_id": figure.asset.id},
    )
