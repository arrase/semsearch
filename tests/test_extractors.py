"""Comprehensive test suite for all multimodal extractors (ZERO OCR)."""

import base64
import io
import json
from pathlib import Path
import subprocess
import wave
import numpy as np
from PIL import Image
import pymupdf as fitz
from semsearch.core.types import Modality, SegmentKind
from semsearch.extractors.code import extract_code, extract_notebook
from semsearch.extractors.docs import extract_doc
from semsearch.extractors.images import extract_image
from semsearch.extractors.media import extract_audio, extract_video
from semsearch.extractors.pdfs import extract_pdf


def test_image_extractor_scaling_and_base64(tmp_path: Path) -> None:
    # Standard image (1024 max)
    img_path = tmp_path / "standard.jpg"
    img = Image.new("RGB", (2000, 1500), color=(120, 80, 40))
    img.save(img_path, format="JPEG")
    ext_img = extract_image(str(img_path), is_screenshot=False)
    assert isinstance(ext_img.payload, dict)
    raw_jpg = base64.b64decode(ext_img.payload["image"])
    assert len(raw_jpg) > 0
    with Image.open(io.BytesIO(raw_jpg)) as im:
        assert im.size == (1024, 768)
        assert im.format == "JPEG"

    # Screenshot (1536 max)
    shot_path = tmp_path / "screen.png"
    shot = Image.new("RGB", (3000, 1000), color=(20, 30, 40))
    shot.save(shot_path, format="PNG")
    ext_shot = extract_image(str(shot_path), is_screenshot=True)
    assert isinstance(ext_shot.payload, dict)
    raw_png = base64.b64decode(ext_shot.payload["image"])
    with Image.open(io.BytesIO(raw_png)) as im:
        assert im.size == (1536, 512)
        assert im.format == "PNG"


def test_pdf_extractor_text_vs_render_zero_ocr(tmp_path: Path) -> None:
    # 1. Text PDF page (>= 200 chars)
    pdf_text_path = tmp_path / "text.pdf"
    doc1 = fitz.open()
    page1 = doc1.new_page()
    text = (
        "Desktop semantic search engine using local Ollama multimodal embeddings. "
        "This long paragraph contains sufficient characters to satisfy the digital text "
        "threshold of two hundred characters, verifying that digital text pages are indexed "
        "directly as text segments without rasterization."
    )
    page1.insert_textbox(fitz.Rect(50, 50, 500, 500), text)
    doc1.save(str(pdf_text_path))
    doc1.close()

    segs, complete = extract_pdf(str(pdf_text_path))
    assert complete is True
    assert len(segs) == 1
    assert segs[0].kind == SegmentKind.PAGE
    assert segs[0].modality == Modality.TEXT
    assert "Desktop semantic search" in segs[0].payload

    # 2. Visual PDF page (< 200 chars)
    pdf_vis_path = tmp_path / "visual.pdf"
    doc2 = fitz.open()
    page2 = doc2.new_page()
    page2.draw_rect(fitz.Rect(100, 100, 400, 400), color=(1, 0, 0), fill=(1, 0.8, 0.8))
    doc2.save(str(pdf_vis_path))
    doc2.close()

    segs_vis, complete_vis = extract_pdf(str(pdf_vis_path))
    assert complete_vis is True
    assert len(segs_vis) == 1
    assert segs_vis[0].kind == SegmentKind.PAGE_IMAGE
    assert segs_vis[0].modality == Modality.IMAGE
    assert isinstance(segs_vis[0].payload, dict)
    assert "image" in segs_vis[0].payload

    # 3. Verify zero OCR in PDFs and images
    for py_file in ("src/semsearch/extractors/images.py", "src/semsearch/extractors/pdfs.py"):
        content = Path(py_file).read_text().lower()
        for forbidden in ("tesseract", "pytesseract", "easyocr", "paddleocr"):
            assert forbidden not in content


def test_doc_extractor_chunking_and_word_snapping(tmp_path: Path) -> None:
    sample_path = tmp_path / "article.md"
    content = " ".join([f"token_{i}" for i in range(400)])
    sample_path.write_text(content, encoding="utf-8")

    segments = extract_doc(str(sample_path), chunk_size=1800, overlap=200)
    assert len(segments) >= 1
    for seg in segments:
        assert seg.kind == SegmentKind.CHUNK
        assert seg.modality == Modality.TEXT
        assert len(seg.payload) <= 1800
        # Words must not be cut in half
        for word in str(seg.payload).split():
            assert word.startswith("token_")


def test_audio_extractor_windowing_and_rms(tmp_path: Path) -> None:
    # 35s tone
    wav_path = tmp_path / "tone.wav"
    sr = 16000
    t = np.linspace(0, 35, 35 * sr, endpoint=False)
    waveform = (np.sin(2 * np.pi * 440 * t) * 15000).astype(np.int16)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(waveform.tobytes())

    windows = extract_audio(str(wav_path))
    assert len(windows) == 2
    assert windows[0].loc == 0.0
    assert windows[0].loc_end == 30.0
    assert windows[1].loc == 25.0
    assert windows[1].loc_end == 35.0

    # Silent file
    silence_path = tmp_path / "silent.wav"
    silent = np.zeros(5 * sr, dtype=np.int16)
    with wave.open(str(silence_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(silent.tobytes())

    silent_windows = extract_audio(str(silence_path))
    assert len(silent_windows) == 0


def test_video_extractor_ahash_shot_deduplication(tmp_path: Path) -> None:
    vid_path = tmp_path / "video.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "smptebars=duration=3:size=160x120",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=4:size=160x120",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=7",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0[outv]",
        "-map",
        "[outv]",
        "-map",
        "2:a",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        str(vid_path),
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert res.returncode == 0

    segs = extract_video(str(vid_path), sample_interval_sec=3.0, hash_threshold=4)
    frames = [s for s in segs if s.kind == SegmentKind.FRAME]
    audio = [s for s in segs if s.kind == SegmentKind.AUDIO]
    assert len(frames) == 2
    assert frames[0].loc == 0.0
    assert frames[1].loc == 3.0
    assert len(audio) >= 1


def test_code_extractor_and_notebook_strip_outputs(tmp_path: Path) -> None:
    # 1. Code stretching and focus definition
    code_path = tmp_path / "engine.py"
    code = (
        "# module doc\n"
        "\n"
        "def start_engine():\n"
        "    return True\n"
        "\n"
        "class Motor:\n"
        "    pass\n"
    )
    code_path.write_text(code, encoding="utf-8")

    stretches = extract_code(str(code_path))
    assert len(stretches) == 1
    assert stretches[0].focus == 3.0
    assert stretches[0].loc == 1.0

    # 2. Notebook output stripping
    nb_path = tmp_path / "notebook.ipynb"
    nb = {
        "cells": [
            {"cell_type": "markdown", "source": ["# Title\n"]},
            {
                "cell_type": "code",
                "source": ["print(12345)\n"],
                "outputs": [{"output_type": "stream", "text": ["SECRET_OUTPUT_VALUE\n"]}],
                "execution_count": 9,
            },
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 2,
    }
    nb_path.write_text(json.dumps(nb), encoding="utf-8")

    nb_stretches = extract_notebook(str(nb_path))
    assert len(nb_stretches) >= 1
    combined = "".join(str(s.payload) for s in nb_stretches)
    assert "# Title" in combined
    assert "print(12345)" in combined
    assert "SECRET_OUTPUT_VALUE" not in combined
