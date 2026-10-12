"""Unit and widget tests for multimodal preview components."""

from pathlib import Path
from PIL import Image
import pymupdf
import pytest
from PySide6.QtWidgets import QApplication

from semsearch.core.types import FileKind, ResultRow, SearchHit
from semsearch.gui.preview import (
    AudioPreviewWidget,
    CodePreviewWidget,
    ImagePreviewWidget,
    PdfPreviewWidget,
    PreviewPane,
    VideoPreviewWidget,
    format_file_size,
    format_tilde_path,
)


def test_format_helpers():
    """Verify size and tilde formatting helpers."""
    assert format_file_size(500) == "500 B"
    assert format_file_size(2048) == "2.0 KB"
    assert format_file_size(5 * 1024 * 1024) == "5.0 MB"

    home_path = str(Path.home() / "Documents" / "test.txt")
    assert format_tilde_path(home_path).startswith("~/")


def test_image_preview_widget(tmp_path: Path, qapp: QApplication):
    """Verify ImagePreviewWidget scales image and updates metadata."""
    img_path = tmp_path / "sample.png"
    img = Image.new("RGB", (400, 300), color="blue")
    img.save(img_path)

    widget = ImagePreviewWidget()
    widget.load_image(str(img_path), "sample.png", size=img_path.stat().st_size, mtime=img_path.stat().st_mtime)

    assert "400 × 300 px" in widget.dimensions_label.text()
    assert widget.image_label.pixmap() is not None
    assert not widget.image_label.pixmap().isNull()
    assert widget.meta_card.name_label.text() == "sample.png"


def test_pdf_preview_widget(tmp_path: Path, qapp: QApplication):
    """Verify PdfPreviewWidget renders page and highlights keywords with navigation."""
    pdf_path = tmp_path / "sample.pdf"
    doc = pymupdf.open()
    page1 = doc.new_page()
    page1.insert_text((72, 72), "Semantic search in Linux desktop engine")
    page2 = doc.new_page()
    page2.insert_text((72, 72), "Second page content here")
    doc.save(str(pdf_path))
    doc.close()

    widget = PdfPreviewWidget()
    widget.load_pdf(str(pdf_path), "sample.pdf", page_number=1, query="search desktop")

    assert "Page 1 of 2" in widget.page_info.text()
    assert widget.page_label.pixmap() is not None
    assert not widget.page_label.pixmap().isNull()
    assert widget.next_btn.isEnabled()
    assert not widget.prev_btn.isEnabled()

    widget._next_page()
    assert "Page 2 of 2" in widget.page_info.text()
    assert not widget.next_btn.isEnabled()
    assert widget.prev_btn.isEnabled()


def test_audio_preview_widget(qapp: QApplication):
    """Verify AudioPreviewWidget waveform visualization and scrub updates."""
    widget = AudioPreviewWidget()
    widget.load_audio("/tmp/track.mp3", "track.mp3", loc=45.0, size=1024 * 1024)

    assert widget._duration >= 45.0
    assert widget.scrub_slider.value() > 0
    assert widget.waveform._progress > 0.0

    widget._on_scrub(500)
    assert widget.waveform._progress == pytest.approx(0.5, 0.01)


def test_video_preview_widget(qapp: QApplication):
    """Verify VideoPreviewWidget poster thumbnail and scrub."""
    widget = VideoPreviewWidget()
    widget.load_video("/tmp/clip.mp4", "clip.mp4", loc=30.0, size=2 * 1024 * 1024)

    assert widget.poster_label.pixmap() is not None
    assert not widget.poster_label.pixmap().isNull()
    assert widget.scrub_slider.value() > 0


def test_code_preview_widget(tmp_path: Path, qapp: QApplication):
    """Verify CodePreviewWidget loads code and configures line gutter and highlight."""
    code_path = tmp_path / "sample.py"
    code_path.write_text("def hello():\n    return 42\n\nprint(hello())\n", encoding="utf-8")

    widget = CodePreviewWidget()
    widget.load_code(str(code_path), "sample.py", line=2)

    assert "def hello():" in widget.editor.toPlainText()
    assert len(widget.editor.extraSelections()) == 1
    assert widget.editor.gutter_width() > 0


def test_preview_pane_stacked_routing(tmp_path: Path, qapp: QApplication):
    """Verify PreviewPane routes multimodal items correctly across stacked indices."""
    pane = PreviewPane()
    assert pane.currentIndex() == 0

    # Image hit
    img_path = tmp_path / "test.jpg"
    Image.new("RGB", (100, 100)).save(img_path)
    hit_img = SearchHit(path=str(img_path), kind=FileKind.IMAGE, score=1.0)
    pane.load_result(hit_img)
    assert pane.currentIndex() == 1

    # PDF hit
    pdf_path = tmp_path / "test.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "PDF content")
    doc.save(str(pdf_path))
    doc.close()
    hit_pdf = SearchHit(path=str(pdf_path), kind=FileKind.PDF, score=1.0, loc=1.0)
    pane.load_result(hit_pdf, query="content")
    assert pane.currentIndex() == 2

    # Audio hit
    hit_audio = SearchHit(path="/tmp/music.mp3", kind=FileKind.AUDIO, score=1.0, loc=10.0)
    pane.load_result(hit_audio)
    assert pane.currentIndex() == 3

    # Video hit
    hit_video = SearchHit(path="/tmp/movie.mp4", kind=FileKind.VIDEO, score=1.0, loc=20.0)
    pane.load_result(hit_video)
    assert pane.currentIndex() == 4

    # Code hit
    py_path = tmp_path / "test.py"
    py_path.write_text("x = 10", encoding="utf-8")
    hit_code = SearchHit(path=str(py_path), kind=FileKind.CODE, score=1.0, focus=1.0)
    pane.load_result(hit_code)
    assert pane.currentIndex() == 5

    # ResultRow loading
    row = ResultRow(title="test.py", folder=str(tmp_path), kind=FileKind.CODE, path=str(py_path))
    pane.load_result(row)
    assert pane.currentIndex() == 5

    # Clear
    pane.clear()
    assert pane.currentIndex() == 0
