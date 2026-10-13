"""Image and screenshot extractor."""

import base64
import io
from PIL import Image, ImageOps
from semsearch.core.types import ExtractedSegment, Modality, SegmentKind


def extract_image(path: str, is_screenshot: bool = False) -> ExtractedSegment:
    """Extract and normalize image using Pillow with strict zero OCR."""
    with Image.open(path) as raw_img:
        img = ImageOps.exif_transpose(raw_img) or raw_img

        max_dim = 1536 if is_screenshot else 1024
        if img.width > max_dim or img.height > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        if is_screenshot or img.mode in ("RGBA", "LA", "P"):
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in getattr(img, "info", {})):
                save_img = img.convert("RGBA")
            elif img.mode == "RGB":
                save_img = img
            else:
                save_img = img.convert("RGB")
            save_img.save(buf, format="PNG")
        else:
            rgb_img = img.convert("RGB") if img.mode != "RGB" else img
            rgb_img.save(buf, format="JPEG", quality=90)

        data_base64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return ExtractedSegment(
            kind=SegmentKind.IMAGE,
            modality=Modality.IMAGE,
            payload={"image": data_base64},
        )
