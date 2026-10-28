"""Multimodal synthetic testbed generator for end-to-end testing."""

import json
import subprocess
import wave
from pathlib import Path

import docx
import numpy as np
import pymupdf as fitz
from PIL import Image, ImageDraw


def create_synthetic_image(path: Path) -> Path:
    """Generate a realistic RGB JPEG photo with visual gradient."""
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = 640, 480
    image = Image.new("RGB", (width, height), color=(255, 140, 0))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / height
        r = int(255 * (1 - 0.4 * ratio))
        g = int(140 * (1 - 0.6 * ratio))
        b = int(40 + 150 * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))
    draw.ellipse([(260, 180), (380, 300)], fill=(255, 240, 150))
    image.save(str(path), format="JPEG", quality=90)
    return path


def create_synthetic_screenshot(path: Path) -> Path:
    """Generate a high-resolution RGBA PNG desktop code editor screenshot."""
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = 1280, 720
    image = Image.new("RGBA", (width, height), color=(30, 30, 35, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([(0, 0), (width, 40)], fill=(45, 45, 50, 255))
    draw.ellipse([(15, 12), (27, 24)], fill=(255, 95, 86, 255))
    draw.ellipse([(35, 12), (47, 24)], fill=(255, 189, 46, 255))
    draw.ellipse([(55, 12), (67, 24)], fill=(39, 201, 63, 255))
    draw.rectangle([(0, 40), (220, height)], fill=(37, 37, 42, 255))
    draw.rectangle([(240, 60), (900, 90)], fill=(80, 160, 240, 255))
    draw.rectangle([(240, 110), (750, 130)], fill=(220, 220, 220, 255))
    draw.rectangle([(240, 150), (600, 170)], fill=(120, 200, 120, 255))
    image.save(str(path), format="PNG")
    return path


def create_synthetic_digital_pdf(path: Path) -> Path:
    """Generate a digital text PDF document containing over 200 characters."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    heading = "Quarterly Systems Engineering and Architectural Report\n\n"
    body = (
        "SemSearch introduces a high performance native semantic search engine for the Linux desktop. "
        "The system coordinates multi-tier background index workers, full-text FTS5 lexical indexing, "
        "vector database integration, and two-stage multimodal extraction without OCR dependencies. "
        "Operating continuously across user document repositories, SemSearch ensures low latency "
        "hybrid ranking combining lexical BM25 matching and normalized vector cosine similarity.\n\n"
        "Deployment verification certifies continuous indexing and minimal battery consumption."
    )
    page.insert_textbox(fitz.Rect(54, 54, 558, 738), heading + body)
    doc.save(str(path))
    doc.close()
    return path


def create_synthetic_visual_pdf(path: Path) -> Path:
    """Generate a visual scanned PDF diagram page without extractable text (no OCR)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.draw_rect(fitz.Rect(80, 80, 300, 220), color=(0.2, 0.4, 0.8), fill=(0.9, 0.95, 1.0), width=2)
    page.draw_rect(fitz.Rect(340, 80, 530, 220), color=(0.8, 0.3, 0.2), fill=(1.0, 0.92, 0.92), width=2)
    page.draw_line(fitz.Point(300, 150), fitz.Point(340, 150), color=(0.1, 0.1, 0.1), width=2)
    page.draw_circle(fitz.Point(300, 400), 80, color=(0.2, 0.7, 0.3), fill=(0.9, 1.0, 0.9), width=2)
    page.insert_text((80, 60), "Figure 1: Architecture Diagram")
    doc.save(str(path))
    doc.close()
    return path


def create_synthetic_text_doc(path: Path) -> Path:
    """Generate a plain text document containing notes and serial lookup tokens."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "Engineering Project Notes and Operations Log\n"
        "System Identifier: SYS-NODE-4096-ALPHA\n"
        "Hardware Serial: ERR-4096-FAIL\n"
        "Account Reference: INV-2026-X99\n\n"
        "Diagnostic reports confirm nominal operation across primary database clusters.\n"
        "Maintenance routines scheduled for weekend window."
    )
    path.write_text(content, encoding="utf-8")
    return path


def create_synthetic_markdown_doc(path: Path) -> Path:
    """Generate a formatted Markdown document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "# SemSearch Architectural Overview\n\n"
        "SemSearch is an intelligent desktop semantic search engine designed for Linux workstations.\n\n"
        "## Highlights\n\n"
        "- Multimodal vector similarity search via local Ollama models.\n"
        "- Full lexical FTS5 retrieval with BM25 ranking.\n"
        "- Strict zero OCR architectural guarantee.\n"
        "- Single instance IPC management.\n\n"
        "```python\n"
        "def query_index(term: str) -> list:\n"
        "    return search_service.search(term)\n"
        "```\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def create_synthetic_word_doc(path: Path) -> Path:
    """Generate a styled Microsoft Word .docx document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = docx.Document()
    doc.add_heading("Executive Strategy Brief", level=1)
    doc.add_paragraph("This brief details the strategy for client deployment and semantic discovery.")
    p = doc.add_paragraph("Key requirements include instant search panel invocation and background crawling.")
    p.runs[0].bold = True
    doc.add_paragraph("Project budget code: PRJ-7788-DECK")
    doc.save(str(path))
    return path


def create_synthetic_audio(path: Path, duration_sec: int = 35) -> Path:
    """Generate a 16 kHz mono PCM WAV audio file with voice-like tones and silence intervals."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 16000
    total_samples = sample_rate * duration_sec
    time_points = np.linspace(0, duration_sec, total_samples, endpoint=False)

    waveform = np.sin(2 * np.pi * 300 * time_points) * 0.4 + np.sin(2 * np.pi * 600 * time_points) * 0.2
    waveform[10 * sample_rate : 20 * sample_rate] = 0.0

    samples_int16 = (waveform * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples_int16.tobytes())
    return path


def create_synthetic_video(path: Path) -> Path:
    """Generate an MP4 video file with visual frames and synchronized audio track."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "smptebars=duration=3:size=320x240:rate=10",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=4:size=320x240:rate=10",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=7:sample_rate=16000",
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
        str(path),
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return path


def create_synthetic_code(path: Path) -> Path:
    """Generate a Python source file containing functions, classes, and symbol definitions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        '"""Core networking and retry management service."""\n\n'
        "import time\n\n\n"
        "def retry_with_backoff(attempts: int = 3, backoff: float = 1.0) -> bool:\n"
        '    """Execute an operation retrying with exponential backoff on failure."""\n'
        "    for attempt in range(attempts):\n"
        "        time.sleep(backoff * (2**attempt))\n"
        "    return True\n\n\n"
        "class TokenBucket:\n"
        '    """Rate limiter implementing the token bucket algorithm."""\n\n'
        "    def __init__(self, capacity: int = 100, fill_rate: float = 10.0) -> None:\n"
        "        self.capacity = capacity\n"
        "        self.fill_rate = fill_rate\n"
        "        self.tokens = float(capacity)\n"
        "        self.last_update = time.time()\n\n"
        "    def consume(self, amount: int = 1) -> bool:\n"
        '        """Consume specified number of tokens if capacity allows."""\n'
        "        if self.tokens >= amount:\n"
        "            self.tokens -= amount\n"
        "            return True\n"
        "        return False\n"
    )
    path.write_text(content, encoding="utf-8")
    return path


def create_synthetic_notebook(path: Path) -> Path:
    """Generate a Jupyter notebook .ipynb with markdown and code cells having stripped outputs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": ["# Analysis Notebook\n", "Exploratory analysis of service metrics."],
            },
            {
                "cell_type": "code",
                "execution_count": 1,
                "metadata": {},
                "outputs": [],
                "source": [
                    "import numpy as np\n",
                    "\n",
                    "def calculate_metrics(values):\n",
                    '    """Calculate summary statistics for given values."""\n',
                    "    return np.mean(values), np.std(values)\n",
                ],
            },
        ],
        "metadata": {
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def generate_testbed(target_dir: Path) -> dict[str, Path]:
    """Generate realistic synthetic test files across all supported modalities."""
    target_dir = Path(target_dir).resolve()
    images_dir = target_dir / "images"
    pdfs_dir = target_dir / "pdfs"
    docs_dir = target_dir / "docs"
    media_dir = target_dir / "media"
    code_dir = target_dir / "code"

    files: dict[str, Path] = {
        "photo_sunset": create_synthetic_image(images_dir / "photo_sunset.jpg"),
        "screenshot_code": create_synthetic_screenshot(images_dir / "screenshot_code.png"),
        "digital_report": create_synthetic_digital_pdf(pdfs_dir / "digital_report.pdf"),
        "scanned_diagram": create_synthetic_visual_pdf(pdfs_dir / "scanned_diagram.pdf"),
        "notes": create_synthetic_text_doc(docs_dir / "notes.txt"),
        "readme": create_synthetic_markdown_doc(docs_dir / "readme.md"),
        "brief": create_synthetic_word_doc(docs_dir / "brief.docx"),
        "speech_audio": create_synthetic_audio(media_dir / "speech_audio.wav"),
        "clip_video": create_synthetic_video(media_dir / "clip_video.mp4"),
        "service_code": create_synthetic_code(code_dir / "service.py"),
        "analysis_notebook": create_synthetic_notebook(code_dir / "analysis.ipynb"),
    }
    return files
