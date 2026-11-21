"""Image, SVG, and screenshot extractor (strict zero OCR)."""

import base64
import io
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageOps

from semsearch.core.types import ExtractedSegment, Modality, SegmentKind

MAX_IMAGE_PIXELS = 100_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

_MAX_IMAGE_DIM = 1024
_MAX_SCREENSHOT_DIM = 1536


def _extract_svg(path: str, max_dim: int) -> ExtractedSegment:
    """Rasterize an SVG document to PNG with PyMuPDF bounded to max_dim."""
    with fitz.open(path) as doc:
        if len(doc) == 0:
            raise ValueError(f"Empty SVG document: {path}")
        page = doc[0]
        rect = page.rect
        max_side = max(rect.width, rect.height)
        scale = (max_dim / max_side) if max_side > max_dim else 1.0
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
        data_base64 = base64.b64encode(pix.tobytes("png")).decode("ascii")
        return ExtractedSegment(
            kind=SegmentKind.IMAGE,
            modality=Modality.IMAGE,
            payload={"image": data_base64},
        )


def _encode_raster(img: Image.Image, *, is_screenshot: bool) -> bytes:
    buf = io.BytesIO()
    if is_screenshot or img.mode in ("RGBA", "LA", "P"):
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            save_img = img.convert("RGBA")
        elif img.mode == "RGB":
            save_img = img
        else:
            save_img = img.convert("RGB")
        save_img.save(buf, format="PNG")
    else:
        rgb_img = img.convert("RGB") if img.mode != "RGB" else img
        rgb_img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def extract_image(path: str, *, is_screenshot: bool = False) -> ExtractedSegment:
    """Extract and normalize raster or SVG image with strict zero OCR."""
    max_dim = _MAX_SCREENSHOT_DIM if is_screenshot else _MAX_IMAGE_DIM
    if Path(path).suffix.lower() == ".svg":
        return _extract_svg(path, max_dim)

    with Image.open(path) as raw_img:
        if raw_img.width * raw_img.height > MAX_IMAGE_PIXELS:
            raise ValueError(f"Image exceeds maximum safe pixel count ({MAX_IMAGE_PIXELS})")
        img = ImageOps.exif_transpose(raw_img) or raw_img
        if img.width > max_dim or img.height > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        raw_bytes = _encode_raster(img, is_screenshot=is_screenshot)

    data_base64 = base64.b64encode(raw_bytes).decode("ascii")
    return ExtractedSegment(
        kind=SegmentKind.IMAGE,
        modality=Modality.IMAGE,
        payload={"image": data_base64},
    )
