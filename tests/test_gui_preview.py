"""Unit and widget tests for the modular asynchronous multimodal preview package."""

import io
import math
import struct
import subprocess
import threading
import time
import wave
import zipfile
from pathlib import Path

import pymupdf
import pytest
from docx import Document
from PIL import Image
from PySide6.QtCore import QEventLoop
from PySide6.QtGui import QImage, QTextFormat
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from semsearch.config import AppConfig
from semsearch.core.types import FileKind, MatchReason, SearchHit
from semsearch.gui.preview import (
    AudioPreviewWidget,
    CodePreviewWidget,
    ImagePreviewWidget,
    MetadataCard,
    PdfPreviewWidget,
    PreviewPane,
    VideoPreviewWidget,
    format_file_size,
    format_tilde_path,
)
from semsearch.gui.preview import loader as preview_loader_mod
from semsearch.gui.preview.code_view import MAX_HIGHLIGHT_SELECTIONS, CodeSyntaxHighlighter
from semsearch.gui.preview.loader import (
    MAX_PREVIEW_TEXT_BYTES,
    PreviewLoader,
    extract_video_frame_cached,
    extract_waveform_bars,
    load_scaled_image,
    read_preview_text,
)
from semsearch.i18n import load_catalog, set_language


def _process_until(qapp: QApplication, predicate: object, timeout_ms: int = 2000) -> bool:
    """Pump the Qt event loop until predicate() returns True or timeout_ms elapses."""
    assert callable(predicate)
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        qapp.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        if predicate():
            return True
        time.sleep(0.01)
    qapp.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
    return bool(predicate())


def _write_two_level_wav(path: Path, duration_sec: float = 1.0, sample_rate: int = 8000) -> None:
    """Create a real WAV file with quiet first half and loud second half for RMS/playback tests."""
    total_samples = int(duration_sec * sample_rate)
    half = total_samples // 2
    samples: list[int] = []
    for i in range(total_samples):
        amp = 2000 if i < half else 24000
        samples.append(int(amp * math.sin(2.0 * math.pi * 440.0 * i / sample_rate)))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def test_format_helpers() -> None:
    """Verify size and tilde formatting helpers."""
    assert format_file_size(500) == "500 B"
    assert format_file_size(2048) == "2.0 KB"
    assert format_file_size(5 * 1024 * 1024) == "5.0 MB"

    home_path = str(Path.home() / "Documents" / "test.txt")
    assert format_tilde_path(home_path).startswith("~/")


def test_image_preview_widget(tmp_path: Path, qapp: QApplication) -> None:
    """Verify ImagePreviewWidget scales raster image and reports true original dimensions."""
    del qapp
    img_path = tmp_path / "sample.png"
    Image.new("RGB", (400, 300), color="blue").save(img_path)

    widget = ImagePreviewWidget()
    widget.load_image(str(img_path), "sample.png", size=img_path.stat().st_size, mtime=img_path.stat().st_mtime)

    assert "400 × 300 px" in widget.dimensions_label.text()
    assert widget.image_label.pixmap() is not None
    assert not widget.image_label.pixmap().isNull()
    assert widget.meta_card.name_label.text() == "sample.png"
    widget.shutdown()


def test_image_preview_svg_and_large_raster_scaling(tmp_path: Path, qapp: QApplication) -> None:
    """Verify ImagePreviewWidget renders .svg via PyMuPDF and load_scaled_image bounds huge rasters."""
    del qapp
    svg_path = tmp_path / "diagram.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="160">'
        '<rect width="320" height="160" fill="#6366F1"/>'
        "</svg>",
        encoding="utf-8",
    )

    widget = ImagePreviewWidget()
    widget.load_image(str(svg_path), "diagram.svg")

    assert "320 × 160 px" in widget.dimensions_label.text()
    assert not widget._pixmap.isNull()
    assert widget.image_label.pixmap() is not None
    assert not widget.image_label.pixmap().isNull()

    # Large raster should decode bounded pixels while preserving original dimensions
    big_path = tmp_path / "huge.png"
    Image.new("RGB", (2400, 1200), color="green").save(big_path)
    img, orig_dims = load_scaled_image(str(big_path), max_side=400)
    assert not img.isNull()
    assert max(img.width(), img.height()) <= 400
    assert orig_dims == (2400, 1200)
    widget.shutdown()


def test_image_preview_animated_gif(tmp_path: Path, qapp: QApplication) -> None:
    """Verify ImagePreviewWidget uses QMovie for animated GIFs and stops it on clear."""
    del qapp
    gif_path = tmp_path / "anim.gif"
    frame1 = Image.new("RGB", (120, 80), color="red")
    frame2 = Image.new("RGB", (120, 80), color="yellow")
    frame1.save(gif_path, save_all=True, append_images=[frame2], duration=100, loop=0)

    widget = ImagePreviewWidget()
    widget.load_image(str(gif_path), "anim.gif")

    loaded_movie = widget._movie
    assert loaded_movie is not None
    assert "120 × 80 px" in widget.dimensions_label.text()

    widget.clear()
    assert widget._movie is None
    widget.shutdown()


def test_pdf_preview_widget(tmp_path: Path, qapp: QApplication) -> None:
    """Verify PdfPreviewWidget renders page, highlights keywords, and navigates with prev/next."""
    del qapp
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

    widget.next_page()
    assert "Page 2 of 2" in widget.page_info.text()
    assert not widget.next_btn.isEnabled()
    assert widget.prev_btn.isEnabled()

    widget.prev_page()
    assert "Page 1 of 2" in widget.page_info.text()
    widget.clear()


def test_pdf_preview_corrupt_and_encrypted_without_crash(tmp_path: Path, qapp: QApplication) -> None:
    """Verify PdfPreviewWidget handles corrupt and password-encrypted PDFs gracefully without crashing."""
    del qapp
    corrupt_path = tmp_path / "corrupt.pdf"
    corrupt_path.write_bytes(b"not a valid pdf header at all")

    widget = PdfPreviewWidget()
    widget.load_pdf(str(corrupt_path), "corrupt.pdf", page_number=1)
    assert widget._doc is None
    assert "corrupt.pdf" in widget.page_label.text()
    assert not widget.prev_btn.isEnabled()
    assert not widget.next_btn.isEnabled()

    encrypted_path = tmp_path / "locked.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "Confidential text")
    doc.save(str(encrypted_path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    doc.close()

    widget.load_pdf(str(encrypted_path), "locked.pdf", page_number=1)
    assert widget._doc is None
    assert "locked.pdf" in widget.page_label.text()
    assert not widget.prev_btn.isEnabled()
    assert not widget.next_btn.isEnabled()


def test_audio_preview_widget_real_duration_playback_and_clear(tmp_path: Path, qapp: QApplication) -> None:
    """Verify AudioPreviewWidget uses real duration, plays/pauses real WAV, and cleans QMediaPlayer on clear."""
    widget = AudioPreviewWidget()

    # Missing file falls back to floor max(loc, 1.0), never synthetic 120.0
    widget.load_audio("/tmp/missing_track.mp3", "missing_track.mp3", loc=45.0, size=1024 * 1024)
    assert widget._duration == pytest.approx(45.0)
    assert widget.scrub_slider.value() > 0
    assert widget.waveform._progress > 0.0

    widget._on_scrub(500)
    assert widget.waveform._progress == pytest.approx(0.5, 0.01)

    # Explicit duration from metadata is respected verbatim
    widget.load_audio("/tmp/missing_track.mp3", "missing_track.mp3", loc=15.0, duration=75.0)
    assert widget._duration == pytest.approx(75.0)
    assert widget.waveform._progress == pytest.approx(15.0 / 75.0, 0.01)

    # Real WAV playback and RMS waveform extraction
    wav_path = tmp_path / "tone.wav"
    _write_two_level_wav(wav_path, duration_sec=1.5)
    bars = extract_waveform_bars(str(wav_path), bar_count=32)
    assert len(bars) == 32
    assert sum(bars[16:]) > sum(bars[:16])

    widget.load_audio(str(wav_path), "tone.wav", loc=0.0, duration=1.5)
    widget.toggle_playback()
    assert _process_until(
        qapp,
        lambda: widget.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
    )
    assert "Pause" in widget.play_btn.text()

    widget.toggle_playback()
    assert _process_until(
        qapp,
        lambda: widget.player.playbackState() == QMediaPlayer.PlaybackState.PausedState,
    )

    widget.clear()
    assert widget.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
    assert widget.player.source().isEmpty()
    assert widget._path == ""
    assert widget._current_pos == 0.0
    widget.shutdown()


def test_video_preview_widget_real_duration_actions_and_clear(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify VideoPreviewWidget uses real duration, emits action_triggered on jump, and releases QMediaPlayer."""
    del qapp
    jumped: list[tuple[str, float]] = []
    triggered: list[bool] = []
    monkeypatch.setattr("semsearch.gui.preview.media_view.open_media_at", lambda p, s: jumped.append((p, s)))

    widget = VideoPreviewWidget()
    widget.action_triggered.connect(lambda: triggered.append(True))

    # Missing file falls back to floor max(loc, 1.0), never synthetic 300.0
    widget.load_video("/tmp/clip.mp4", "clip.mp4", loc=30.0, size=2 * 1024 * 1024)
    assert widget._duration == pytest.approx(30.0)
    assert widget.poster_label.pixmap() is not None
    assert not widget.poster_label.pixmap().isNull()
    assert widget.scrub_slider.value() > 0

    # Real duration passed from hit.info["duration"]
    widget.load_video("/tmp/clip.mp4", "clip.mp4", loc=30.0, duration=90.0)
    assert widget._duration == pytest.approx(90.0)
    assert widget.time_label.text() == "0:30 / 1:30"

    widget.jump_btn.click()
    assert jumped == [("/tmp/clip.mp4", 30.0)]
    assert triggered == [True]

    widget.clear()
    assert widget.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
    assert widget.player.source().isEmpty()
    assert widget._poster_pixmap.isNull()
    assert widget._path == ""
    widget.shutdown()


def test_video_preview_debounced_scrub_frame_refresh(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify rapid scrub slider changes coalesce into one debounced async frame extraction."""
    video_path = tmp_path / "scrub.mp4"
    video_path.write_bytes(b"placeholder")
    requested_ts: list[float] = []

    def fake_extract(path: str, ts: float) -> QImage:
        del path
        requested_ts.append(round(ts, 1))
        img = QImage(320, 180, QImage.Format.Format_RGB32)
        img.fill(0x336699)
        return img

    monkeypatch.setattr("semsearch.gui.preview.media_view.extract_video_frame_cached", fake_extract)

    widget = VideoPreviewWidget()
    widget.load_video(str(video_path), "scrub.mp4", loc=0.0, duration=100.0)
    assert _process_until(qapp, lambda: len(requested_ts) >= 1)

    requested_ts.clear()
    widget.scrub_slider.setValue(200)
    widget.scrub_slider.setValue(400)
    widget.scrub_slider.setValue(600)
    assert _process_until(qapp, lambda: len(requested_ts) == 1, timeout_ms=1000)
    assert requested_ts == [60.0]
    widget.shutdown()


def test_video_frame_disk_cache_and_pruning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify extract_video_frame_cached writes to cache, reuses cached PNG, and prunes beyond limit."""
    cache_dir = tmp_path / "thumbs"
    monkeypatch.setattr(preview_loader_mod, "THUMBS_CACHE_DIR", cache_dir)
    monkeypatch.setattr(preview_loader_mod, "MAX_CACHED_THUMBS", 3)

    video_path = tmp_path / "demo.mp4"
    video_path.write_bytes(b"fake video bytes")

    buf = io.BytesIO()
    Image.new("RGB", (160, 90), color="purple").save(buf, format="PNG")
    png_bytes = buf.getvalue()

    calls = 0

    def fake_run(
        cmd: list[str],
        *,
        capture_output: bool,
        timeout: float,
        check: bool,
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal calls
        del capture_output, timeout, check
        calls += 1
        return subprocess.CompletedProcess(cmd, 0, stdout=png_bytes, stderr=b"")

    monkeypatch.setattr("semsearch.gui.preview.loader.shutil.which", lambda _name: "/usr/bin/ffmpeg")
    monkeypatch.setattr("semsearch.gui.preview.loader.subprocess.run", fake_run)

    # First call extracts and populates disk cache
    img1 = extract_video_frame_cached(str(video_path), 12.34)
    assert not img1.isNull()
    assert img1.width() == 160
    assert calls == 1
    assert len(list(cache_dir.glob("*.png"))) == 1

    # Second call at same rounded timestamp hits disk cache without calling ffmpeg
    img2 = extract_video_frame_cached(str(video_path), 12.31)
    assert not img2.isNull()
    assert calls == 1

    # Generating 4 more unique timestamps triggers pruning down to MAX_CACHED_THUMBS (3)
    for ts in (1.0, 2.0, 3.0, 4.0):
        frame = extract_video_frame_cached(str(video_path), ts)
        assert not frame.isNull()
    assert len(list(cache_dir.glob("*.png"))) == 3


def test_preview_loader_discards_stale_requests(qapp: QApplication) -> None:
    """Verify PreviewLoader ignores superseded request_id results from slower earlier tasks."""
    loader = PreviewLoader(max_threads=2)
    release_first = threading.Event()
    received: list[object] = []

    def slow_first() -> str:
        release_first.wait(timeout=2.0)
        return "stale-first"

    loader.submit("video_frame", slow_first, received.append)
    loader.submit("video_frame", lambda: "fresh-second", received.append)
    release_first.set()

    assert _process_until(qapp, lambda: "fresh-second" in received)
    loader.shutdown()
    qapp.processEvents()
    assert received == ["fresh-second"]


def test_code_preview_widget(tmp_path: Path, qapp: QApplication) -> None:
    """Verify CodePreviewWidget loads code, highlights line, and applies CodeSyntaxHighlighter."""
    del qapp
    code_path = tmp_path / "sample.py"
    code_path.write_text(
        '# Module comment\ndef hello():\n    url = "http://example.com"\n    return 42\n',
        encoding="utf-8",
    )

    widget = CodePreviewWidget()
    widget.load_code(str(code_path), "sample.py", line=2)

    assert "def hello():" in widget.editor.toPlainText()
    assert len(widget.editor.extraSelections()) == 1
    assert widget.editor.gutter_width() > 0
    assert isinstance(widget.editor.highlighter, CodeSyntaxHighlighter)

    # Verify syntax highlighter applied formats to comment, keyword, definition, and string
    doc = widget.editor.document()
    block_comment = doc.findBlockByNumber(0)
    block_def = doc.findBlockByNumber(1)
    block_str = doc.findBlockByNumber(2)
    assert any(r.format.fontItalic() for r in block_comment.layout().formats())
    assert len(block_def.layout().formats()) >= 2
    # String containing // must not be miscolored as a comment (italic)
    str_formats = block_str.layout().formats()
    assert str_formats
    assert not any(r.format.fontItalic() for r in str_formats)
    widget.shutdown()


def test_code_preview_truncation_cap_and_diacritics(tmp_path: Path, qapp: QApplication) -> None:
    """Verify 256 KB truncation, 200 highlight cap, and diacritic-insensitive highlighting."""
    del qapp
    # 1. Truncation at 256 KB
    huge_path = tmp_path / "huge.log"
    huge_path.write_bytes(b"a" * (MAX_PREVIEW_TEXT_BYTES + 20_000))
    preview_text = read_preview_text(str(huge_path))
    assert preview_text.endswith("\n…")
    assert len(preview_text) == MAX_PREVIEW_TEXT_BYTES + 2

    # 2. Cap at 200 ExtraSelection highlights
    many_path = tmp_path / "many.txt"
    many_path.write_text(("alpha " * 350) + "\n", encoding="utf-8")
    widget = CodePreviewWidget()
    widget.load_code(str(many_path), "many.txt", query="alpha")
    assert len(widget.editor.extraSelections()) == MAX_HIGHLIGHT_SELECTIONS

    # 3. Diacritic-insensitive highlighting: "cancion" highlights "canción"
    accent_path = tmp_path / "musica.txt"
    accent_path.write_text("Escucha esta canción inolvidable.\n", encoding="utf-8")
    widget.load_code(str(accent_path), "musica.txt", query="cancion")
    selections = widget.editor.extraSelections()
    assert len(selections) == 1
    assert selections[0].cursor.selectedText() == "canción"
    widget.shutdown()


def test_preview_pane_stacked_routing(tmp_path: Path, qapp: QApplication) -> None:
    """Verify PreviewPane routes multimodal items (including .svg) correctly across stacked indices."""
    del qapp
    pane = PreviewPane()
    assert pane.currentIndex() == 0

    # Image hit
    img_path = tmp_path / "test.jpg"
    Image.new("RGB", (100, 100)).save(img_path)
    pane.load_result(SearchHit(path=str(img_path), kind=FileKind.IMAGE, score=1.0))
    assert pane.currentIndex() == 1

    # SVG hit routes to image view (index 1)
    svg_path = tmp_path / "icon.svg"
    svg_path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"/>', encoding="utf-8")
    pane.load_result(SearchHit(path=str(svg_path), kind=FileKind.IMAGE, score=1.0))
    assert pane.currentIndex() == 1

    # PDF hit
    pdf_path = tmp_path / "test.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "PDF content")
    doc.save(str(pdf_path))
    doc.close()
    pane.load_result(SearchHit(path=str(pdf_path), kind=FileKind.PDF, score=1.0, loc=1.0), query="content")
    assert pane.currentIndex() == 2

    # Audio hit
    pane.load_result(
        SearchHit(path="/tmp/music.mp3", kind=FileKind.AUDIO, score=1.0, loc=10.0, info={"duration": 80.0})
    )
    assert pane.currentIndex() == 3
    assert pane.audio_view._duration == pytest.approx(80.0)

    # Video hit
    pane.load_result(
        SearchHit(path="/tmp/movie.mp4", kind=FileKind.VIDEO, score=1.0, loc=20.0, info={"duration": 140.0})
    )
    assert pane.currentIndex() == 4
    assert pane.video_view._duration == pytest.approx(140.0)

    # Code hit
    py_path = tmp_path / "test.py"
    py_path.write_text("x = 10", encoding="utf-8")
    pane.load_result(SearchHit(path=str(py_path), kind=FileKind.CODE, score=1.0, focus=1.0))
    assert pane.currentIndex() == 5

    # Clear & shutdown
    pane.shutdown()
    assert pane.currentIndex() == 0


def test_code_preview_widget_document_formats(tmp_path: Path, qapp: QApplication) -> None:
    """Verify CodePreviewWidget extracts text from documents and falls back to excerpt on corrupt archives."""
    del qapp
    widget = CodePreviewWidget()
    widget.load_code(str(tmp_path / "nonexistent.docx"), "nonexistent.docx")
    assert widget.editor.toPlainText() == ""

    # Corrupt DOCX falls back cleanly to excerpt
    corrupt_docx = tmp_path / "broken.docx"
    corrupt_docx.write_bytes(b"not a zip")
    widget.load_code(str(corrupt_docx), "broken.docx", excerpt="Fallback excerpt")
    assert widget.editor.toPlainText() == "Fallback excerpt"

    # Valid DOCX
    docx_path = tmp_path / "sample.docx"
    doc = Document()
    doc.add_heading("Docx Header", level=1)
    doc.add_paragraph("Docx preview paragraph text.")
    doc.save(str(docx_path))

    widget.load_code(str(docx_path), "sample.docx")
    content = widget.editor.toPlainText()
    assert "Docx Header" in content
    assert "Docx preview paragraph text." in content
    assert "[Content_Types].xml" not in content

    # ODT format
    odt_path = tmp_path / "sample.odt"
    content_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
                             xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
      <office:body>
        <office:text>
          <text:p>ODT extracted body text.</text:p>
        </office:text>
      </office:body>
    </office:document-content>"""
    with zipfile.ZipFile(odt_path, "w") as zf:
        zf.writestr("content.xml", content_xml)

    widget.load_code(str(odt_path), "sample.odt")
    odt_content = widget.editor.toPlainText()
    assert "ODT extracted body text." in odt_content
    assert "office:document-content" not in odt_content

    # Uppercase extension (.DOCX)
    upper_docx = tmp_path / "UPPER.DOCX"
    doc_upper = Document()
    doc_upper.add_paragraph("Uppercase DOCX content.")
    doc_upper.save(str(upper_docx))
    widget.load_code(str(upper_docx), "UPPER.DOCX")
    assert "Uppercase DOCX content." in widget.editor.toPlainText()

    # RTF format
    rtf_path = tmp_path / "sample.rtf"
    rtf_path.write_text(
        r"{\rtf1\ansi\deff0 {\fonttbl {\f0 Courier;}}\f0\fs20 RTF extracted body text.\par}",
        encoding="utf-8",
    )
    widget.load_code(str(rtf_path), "sample.rtf")
    assert "RTF extracted body text." in widget.editor.toPlainText()

    # HTML format
    html_path = tmp_path / "sample.html"
    html_path.write_text("<html><body><h1>HTML Heading</h1><p>HTML paragraph text.</p></body></html>", encoding="utf-8")
    widget.load_code(str(html_path), "sample.html")
    html_content = widget.editor.toPlainText()
    assert "HTML Heading" in html_content
    assert "HTML paragraph text." in html_content
    assert "<h1>" not in html_content
    widget.shutdown()


def test_preview_pane_document_routing(tmp_path: Path, qapp: QApplication) -> None:
    """Verify PreviewPane routes document files to text preview with extracted text and term highlights."""
    del qapp
    docx_path = tmp_path / "report.docx"
    doc = Document()
    doc.add_paragraph("Annual report content.")
    doc.save(str(docx_path))

    pane = PreviewPane()
    hit = SearchHit(path=str(docx_path), kind=FileKind.DOC, score=1.0)
    pane.load_result(hit, query="Annual")

    assert pane.currentIndex() == 5
    assert "Annual report content." in pane.code_view.editor.toPlainText()
    selections = pane.code_view.editor.extraSelections()
    assert len(selections) == 1
    assert not selections[0].format.boolProperty(QTextFormat.Property.FullWidthSelection)
    assert pane.code_view.editor.lineWrapMode() == QPlainTextEdit.LineWrapMode.WidgetWidth
    pane.shutdown()


def test_preview_pane_doc_and_code_highlight_behavior(tmp_path: Path, qapp: QApplication) -> None:
    """Verify FileKind.DOC semantic match softly highlights excerpt while FileKind.CODE keeps full-width line."""
    del qapp
    md_path = tmp_path / "Bienvenido.md"
    md_path.write_text(
        "Esta es su nueva *bóveda*.\n\nSegunda línea con contenido sobre un pozo profundo.\n",
        encoding="utf-8",
    )

    pane = PreviewPane()

    # Semantic match on .md where query term 'agujero' is not present literally
    hit_md = SearchHit(
        path=str(md_path),
        kind=FileKind.DOC,
        score=0.8,
        excerpt="Segunda línea con contenido sobre un pozo profundo.",
    )
    pane.load_result(hit_md, query="agujero")
    assert pane.currentIndex() == 5
    doc_selections = pane.code_view.editor.extraSelections()
    assert len(doc_selections) == 1
    assert not doc_selections[0].format.boolProperty(QTextFormat.Property.FullWidthSelection)
    assert "Segunda línea" in doc_selections[0].cursor.selectedText()
    assert pane.code_view.editor.lineWrapMode() == QPlainTextEdit.LineWrapMode.WidgetWidth
    assert pane.code_view.editor.textCursor().blockNumber() == 2

    # Code match with focus line > 0 retains full-width yellow line highlight and NoWrap
    py_path = tmp_path / "engine.py"
    py_path.write_text("import os\n\ndef compute_score():\n    return 1.0\n", encoding="utf-8")
    hit_code = SearchHit(path=str(py_path), kind=FileKind.CODE, score=0.9, focus=3.0)
    pane.load_result(hit_code, query="compute")
    code_selections = pane.code_view.editor.extraSelections()
    assert len(code_selections) == 2
    assert code_selections[0].format.boolProperty(QTextFormat.Property.FullWidthSelection)
    assert not code_selections[1].format.boolProperty(QTextFormat.Property.FullWidthSelection)
    assert pane.code_view.editor.lineWrapMode() == QPlainTextEdit.LineWrapMode.NoWrap
    pane.shutdown()


def test_metadata_card_open_uses_execute_hit_action_and_translates_reason(
    tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify MetadataCard Open invokes execute_hit_action preserving page/line/timestamp and translates reason."""
    del qapp
    executed: list[tuple[SearchHit, AppConfig | None]] = []
    revealed: list[str] = []
    pane_triggers: list[bool] = []

    def fake_execute(hit: SearchHit, cfg: AppConfig | None) -> bool:
        executed.append((hit, cfg))
        return True

    monkeypatch.setattr("semsearch.gui.preview.metadata.execute_hit_action", fake_execute)
    monkeypatch.setattr("semsearch.gui.preview.metadata.reveal_in_file_manager", revealed.append)

    cfg = AppConfig(code_editor="zed")
    pane = PreviewPane(config=cfg)
    pane.action_triggered.connect(lambda: pane_triggers.append(True))

    # 1. Code hit with focus line
    py_path = tmp_path / "app.py"
    py_path.write_text("def run():\n    return 1\n", encoding="utf-8")
    hit_code = SearchHit(path=str(py_path), kind=FileKind.CODE, score=1.0, z=3.0, focus=2.0)
    set_language("es")
    try:
        pane.load_result(hit_code)
        assert pane.code_view.meta_card.reason_badge.text() == load_catalog("es")[MatchReason.MEANING]
        pane.code_view.meta_card.open_btn.click()
        pane.code_view.meta_card.reveal_btn.click()
    finally:
        set_language("en")

    # 2. PDF hit with page number
    pdf_path = tmp_path / "manual.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "Page 1")
    doc.new_page().insert_text((50, 50), "Page 2")
    doc.save(str(pdf_path))
    doc.close()
    hit_pdf = SearchHit(path=str(pdf_path), kind=FileKind.PDF, score=1.0, loc=2.0)
    pane.load_result(hit_pdf)
    pane.pdf_view.meta_card.open_btn.click()

    # 3. Video hit with timestamp
    hit_video = SearchHit(path="/tmp/talk.mp4", kind=FileKind.VIDEO, score=1.0, loc=42.5)
    pane.load_result(hit_video)
    pane.video_view.meta_card.open_btn.click()

    assert executed == [(hit_code, cfg), (hit_pdf, cfg), (hit_video, cfg)]
    assert revealed == [str(py_path)]
    assert len(pane_triggers) == 4

    # Standalone MetadataCard without bound SearchHit still opens via open_file fallback
    fallback_opened: list[str] = []
    monkeypatch.setattr("semsearch.gui.preview.metadata.open_file", fallback_opened.append)
    card = MetadataCard()
    card.update_metadata("notes.txt", "/tmp/notes.txt")
    card.open_btn.click()
    assert fallback_opened == ["/tmp/notes.txt"]
    pane.shutdown()


def test_preview_pane_clear_releases_resources(tmp_path: Path, qapp: QApplication) -> None:
    """Verify PreviewPane.clear() closes pymupdf.Document, stops QMediaPlayer, and clears buffers."""
    del qapp
    pane = PreviewPane()

    # Load Image
    img_path = tmp_path / "clear_test.png"
    Image.new("RGB", (120, 80), color="red").save(img_path)
    pane.load_result(SearchHit(path=str(img_path), kind=FileKind.IMAGE, score=1.0))
    assert not pane.image_view._pixmap.isNull()

    # Load PDF
    pdf_path = tmp_path / "clear_test.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "Release test page")
    doc.save(str(pdf_path))
    doc.close()
    pane.load_result(SearchHit(path=str(pdf_path), kind=FileKind.PDF, score=1.0, loc=1.0), query="Release")
    pdf_doc = pane.pdf_view._doc
    assert pdf_doc is not None
    assert not pdf_doc.is_closed
    assert not pane.pdf_view._rendered_pixmap.isNull()

    # Load Audio
    pane.load_result(SearchHit(path="/tmp/clear_audio.mp3", kind=FileKind.AUDIO, score=1.0, loc=15.0))
    assert pane.audio_view._path == "/tmp/clear_audio.mp3"

    # Load Video
    pane.load_result(SearchHit(path="/tmp/clear_video.mp4", kind=FileKind.VIDEO, score=1.0, loc=25.0))
    assert not pane.video_view._poster_pixmap.isNull()
    assert pane.video_view._path == "/tmp/clear_video.mp4"

    # Load Code
    py_path = tmp_path / "clear_code.py"
    py_path.write_text("def cleanup():\n    return True\n", encoding="utf-8")
    pane.load_result(SearchHit(path=str(py_path), kind=FileKind.CODE, score=1.0, focus=1.0), query="cleanup")
    assert pane.code_view.editor.toPlainText() != ""
    assert len(pane.code_view.editor.extraSelections()) > 0

    # Clear all views
    pane.clear()

    assert pane.currentIndex() == 0
    assert pane.image_view._pixmap.isNull()
    assert pane.image_view.dimensions_label.text() == ""
    assert pdf_doc.is_closed
    assert pane.pdf_view._doc is None
    assert pane.pdf_view._rendered_pixmap.isNull()
    assert pane.pdf_view._total_pages == 0
    assert pane.pdf_view._current_page == 0
    assert pane.pdf_view._query_terms == []
    assert pane.audio_view._path == ""
    assert pane.audio_view._current_pos == 0.0
    assert pane.audio_view.player.source().isEmpty()
    assert pane.audio_view.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
    assert pane.video_view._poster_pixmap.isNull()
    assert pane.video_view._path == ""
    assert pane.video_view._current_pos == 0.0
    assert pane.video_view.player.source().isEmpty()
    assert pane.code_view.editor.toPlainText() == ""
    assert pane.code_view.editor.extraSelections() == []
    pane.shutdown()
