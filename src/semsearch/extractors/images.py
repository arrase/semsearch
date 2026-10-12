"""Image and screenshot extractor."""

import base64
from dataclasses import dataclass
import io
from PIL import Image, ImageOps
from semsearch.core.types import ExtractedSegment, Modality, SegmentKind


@dataclass
class ExtractedImage:
    """Extracted and downscaled image ready for embedding."""

    data_base64: str
    width: int
    height: int
    format: str

    def to_segment(self, kind: SegmentKind = SegmentKind.IMAGE) -> ExtractedSegment:
        """Convert extracted image into an ExtractedSegment."""
        return ExtractedSegment(
            kind=kind,
            modality=Modality.IMAGE,
            payload={"image": self.data_base64},
        )


def extract_image(path: str, is_screenshot: bool = False) -> ExtractedImage:
    """Extract and normalize image using Pillow with strict zero OCR."""
    with Image.open(path) as raw_img:
        img = ImageOps.exif_transpose(raw_img) or raw_img

        max_dim = 1536 if is_screenshot else 1024
        if img.width > max_dim or img.height > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        if is_screenshot or img.mode in ("RGBA", "LA", "P"):
            fmt = "PNG"
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in getattr(img, "info", {})):
                save_img = img.convert("RGBA")
            elif img.mode == "RGB":
                save_img = img
            else:
                save_img = img.convert("RGB")
            save_img.save(buf, format="PNG")
        else:
            fmt = "JPEG"
            rgb_img = img.convert("RGB") if img.mode != "RGB" else img
            rgb_img.save(buf, format="JPEG", quality=90)

        data_base64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return ExtractedImage(
            data_base64=data_base64,
            width=img.width,
            height=img.height,
            format=fmt.lower(),
        )


def extract_image_segment(path: str, is_screenshot: bool = False) -> ExtractedSegment:
    """Extract image directly as an ExtractedSegment."""
    img = extract_image(path, is_screenshot=is_screenshot)
    return img.to_segment()
