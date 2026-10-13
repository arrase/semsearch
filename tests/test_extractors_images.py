"""Unit tests for image and screenshot extractor (zero OCR)."""

import base64
import io
from pathlib import Path
from PIL import Image
from semsearch.core.types import Modality, SegmentKind
from semsearch.extractors.images import extract_image


def test_extract_standard_image_downscaling(tmp_path: Path) -> None:
    img_path = tmp_path / "large_photo.jpg"
    img = Image.new("RGB", (2000, 1000), color=(100, 150, 200))
    img.save(img_path, format="JPEG")

    seg = extract_image(str(img_path), is_screenshot=False)
    assert isinstance(seg.payload, dict)
    decoded = base64.b64decode(seg.payload["image"])
    assert len(decoded) > 0
    with Image.open(io.BytesIO(decoded)) as decoded_img:
        assert decoded_img.size == (1024, 512)
        assert decoded_img.format == "JPEG"


def test_extract_screenshot_downscaling(tmp_path: Path) -> None:
    shot_path = tmp_path / "desktop_shot.png"
    img = Image.new("RGB", (3000, 1500), color=(50, 50, 50))
    img.save(shot_path, format="PNG")

    seg = extract_image(str(shot_path), is_screenshot=True)
    assert isinstance(seg.payload, dict)
    decoded = base64.b64decode(seg.payload["image"])
    with Image.open(io.BytesIO(decoded)) as decoded_img:
        assert decoded_img.size == (1536, 768)
        assert decoded_img.format == "PNG"


def test_extract_rgba_transparency(tmp_path: Path) -> None:
    rgba_path = tmp_path / "transparent_icon.png"
    img = Image.new("RGBA", (200, 200), color=(255, 0, 0, 128))
    img.save(rgba_path, format="PNG")

    seg = extract_image(str(rgba_path), is_screenshot=False)
    assert isinstance(seg.payload, dict)
    decoded = base64.b64decode(seg.payload["image"])
    with Image.open(io.BytesIO(decoded)) as decoded_img:
        assert decoded_img.format == "PNG"
        assert decoded_img.size == (200, 200)


def test_image_to_segment(tmp_path: Path) -> None:
    img_path = tmp_path / "sample.jpg"
    img = Image.new("RGB", (300, 200), color=(20, 40, 60))
    img.save(img_path, format="JPEG")

    seg = extract_image(str(img_path))
    assert seg.kind == SegmentKind.IMAGE
    assert seg.modality == Modality.IMAGE
    assert isinstance(seg.payload, dict)
    assert "image" in seg.payload
    assert len(seg.payload["image"]) > 0


def test_extract_cmyk_screenshot_to_png(tmp_path: Path) -> None:
    cmyk_path = tmp_path / "cmyk_print.jpg"
    img = Image.new("CMYK", (400, 300), color=(100, 50, 0, 10))
    img.save(cmyk_path, format="JPEG")

    seg = extract_image(str(cmyk_path), is_screenshot=True)
    assert isinstance(seg.payload, dict)
    decoded = base64.b64decode(seg.payload["image"])
    assert len(decoded) > 0

    with Image.open(io.BytesIO(decoded)) as decoded_img:
        assert decoded_img.format == "PNG"
        assert decoded_img.mode == "RGB"
        assert decoded_img.size == (400, 300)


def test_zero_ocr_invariant() -> None:
    images_code = Path("src/semsearch/extractors/images.py").read_text()
    forbidden = ["tesseract", "pytesseract", "easyocr", "paddleocr"]
    for word in forbidden:
        assert word not in images_code.lower()
