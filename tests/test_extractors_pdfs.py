"""Unit tests for PDF extractor (two-stage, text vs visual render, zero OCR)."""

import base64
from pathlib import Path
import pymupdf as fitz
from semsearch.core.types import Modality, SegmentKind
from semsearch.extractors.pdfs import extract_pdf


def test_extract_pdf_digital_text(tmp_path: Path) -> None:
    pdf_path = tmp_path / "text_doc.pdf"
    doc = fitz.open()
    page = doc.new_page()

    sample_text = (
        "Semantic search enables discovery of desktop content based on contextual meaning "
        "rather than exact literal keyword matches. This sentence is added to guarantee that "
        "the page contains well over two hundred characters of rich and meaningful English "
        "text for testing the extractor pipeline thoroughly."
    )
    assert len(sample_text) >= 200

    page.insert_textbox(fitz.Rect(50, 50, 500, 500), sample_text)
    doc.save(str(pdf_path))
    doc.close()

    segments, is_complete = extract_pdf(str(pdf_path))
    assert is_complete is True
    assert len(segments) == 1
    seg = segments[0]
    assert seg.kind == SegmentKind.PAGE
    assert seg.modality == Modality.TEXT
    assert seg.loc == 1.0
    assert "Semantic search enables" in seg.payload


def test_extract_pdf_visual_rendering(tmp_path: Path) -> None:
    pdf_path = tmp_path / "scanned_doc.pdf"
    doc = fitz.open()
    page = doc.new_page()
    # Add minimal text (< 200 characters) representing a scanned page or slide
    page.insert_text(fitz.Point(50, 50), "Figure 1: Diagram")
    # Draw a visual shape
    page.draw_rect(fitz.Rect(50, 100, 300, 300), color=(0, 0, 1), fill=(0.8, 0.8, 1))
    doc.save(str(pdf_path))
    doc.close()

    segments, is_complete = extract_pdf(str(pdf_path))
    assert is_complete is True
    assert len(segments) == 1
    seg = segments[0]
    assert seg.kind == SegmentKind.PAGE_IMAGE
    assert seg.modality == Modality.IMAGE
    assert seg.loc == 1.0
    assert isinstance(seg.payload, dict)
    assert "image" in seg.payload
    assert seg.excerpt == "Figure 1: Diagram"
    decoded = base64.b64decode(seg.payload["image"])
    assert len(decoded) > 0


def test_extract_pdf_whitespace_and_control_chars_fallback(tmp_path: Path) -> None:
    """Verify pages with only whitespace or control characters fall back to Page N (visual)."""
    pdf_path = tmp_path / "whitespace_only.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(fitz.Point(50, 50), "   \t\n   --- ... ___   \n")
    doc.save(str(pdf_path))
    doc.close()

    segments, is_complete = extract_pdf(str(pdf_path))
    assert is_complete is True
    assert len(segments) == 1
    seg = segments[0]
    assert seg.kind == SegmentKind.PAGE_IMAGE
    assert seg.excerpt == "Page 1 (visual)"


def test_extract_pdf_two_stage_pagination(tmp_path: Path) -> None:
    pdf_path = tmp_path / "long_doc.pdf"
    doc = fitz.open()
    for i in range(35):
        page = doc.new_page()
        page.insert_text(fitz.Point(50, 50), f"Short slide {i + 1}")
    doc.save(str(pdf_path))
    doc.close()

    # Pass 1: first 30 pages
    pass1_segments, pass1_complete = extract_pdf(str(pdf_path), start_page=0, max_pages=30)
    assert len(pass1_segments) == 30
    assert pass1_complete is False
    assert pass1_segments[0].loc == 1.0
    assert pass1_segments[-1].loc == 30.0

    # Pass 2: remaining 5 pages
    pass2_segments, pass2_complete = extract_pdf(str(pdf_path), start_page=30, max_pages=30)
    assert len(pass2_segments) == 5
    assert pass2_complete is True
    assert pass2_segments[0].loc == 31.0
    assert pass2_segments[-1].loc == 35.0


def test_pdf_zero_ocr_invariant() -> None:
    code = Path("src/semsearch/extractors/pdfs.py").read_text()
    forbidden = ["tesseract", "pytesseract", "easyocr", "paddleocr"]
    for word in forbidden:
        assert word not in code.lower()
