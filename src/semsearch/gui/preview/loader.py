"""Background preview I/O helpers, thumbnail disk cache, and QThreadPool task coordinator."""

import hashlib
import shutil
import subprocess
import threading
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pymupdf
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageReader

from semsearch.extractors.docs import read_doc_text

THUMBS_CACHE_DIR = Path.home() / ".cache" / "semsearch" / "thumbs"
MAX_CACHED_THUMBS = 200
MAX_PREVIEW_TEXT_BYTES = 262_144

_DOC_EXTRACT_SUFFIXES = frozenset({".docx", ".odt", ".rtf", ".html", ".htm"})
_FFMPEG_FRAME_TIMEOUT = 2.5
_FFMPEG_WAVEFORM_TIMEOUT = 4.0
_WAVEFORM_SAMPLE_RATE = 4000
_WAVEFORM_MAX_SECONDS = "600"
_DEFAULT_WAVEFORM_LEVEL = 0.25
_MIN_WAVEFORM_BAR = 0.08
_MIN_AUDIO_BYTES = 2


def _default_waveform_bars(bar_count: int) -> list[float]:
    return [_DEFAULT_WAVEFORM_LEVEL] * max(0, bar_count)


def _thumb_cache_path(path: str, mtime: float, timestamp: float) -> Path:
    raw_key = f"{path}:{mtime}:{round(max(0.0, timestamp), 1)}"
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]
    return THUMBS_CACHE_DIR / f"{digest}.png"


def _prune_thumb_cache(cache_dir: Path) -> None:
    files = list(cache_dir.glob("*.png"))
    if len(files) <= MAX_CACHED_THUMBS:
        return
    files.sort(key=lambda p: p.stat().st_mtime)
    for stale in files[: len(files) - MAX_CACHED_THUMBS]:
        stale.unlink(missing_ok=True)


def _write_thumb_cache(cache_path: Path, png_bytes: bytes) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_name(f"{cache_path.stem}.{threading.get_ident()}.tmp")
    tmp_path.write_bytes(png_bytes)
    tmp_path.replace(cache_path)
    _prune_thumb_cache(cache_path.parent)


def _load_svg_image(path: str, max_side: int) -> tuple[QImage, tuple[int, int]]:
    try:
        with pymupdf.open(path) as doc:
            if len(doc) == 0:
                return QImage(), (0, 0)
            page = doc[0]
            rect = page.rect
            orig_w = max(1, round(rect.width))
            orig_h = max(1, round(rect.height))
            scale = max_side / max(rect.width, rect.height, 1.0)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=True)
            img = QImage(
                pix.samples,
                pix.width,
                pix.height,
                pix.stride,
                QImage.Format.Format_RGBA8888,
            ).copy()
    except (pymupdf.FileDataError, RuntimeError, ValueError, OSError):
        return QImage(), (0, 0)
    return img, (orig_w, orig_h)


def _load_raster_image(path: str, max_side: int) -> tuple[QImage, tuple[int, int]]:
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    raw_size = reader.size()

    if raw_size.isValid() and raw_size.width() > 0 and raw_size.height() > 0:
        orig_w, orig_h = raw_size.width(), raw_size.height()
        if max(orig_w, orig_h) > max_side:
            reader.setScaledSize(raw_size.scaled(max_side, max_side, Qt.AspectRatioMode.KeepAspectRatio))
        img = reader.read()
        if img.isNull():
            return QImage(), (0, 0)
        if orig_w != orig_h and (img.width() > img.height()) != (orig_w > orig_h):
            orig_w, orig_h = orig_h, orig_w
        return img, (orig_w, orig_h)

    img = reader.read()
    if img.isNull():
        return QImage(), (0, 0)
    orig_w, orig_h = img.width(), img.height()
    if max(orig_w, orig_h) > max_side:
        img = img.scaled(
            max_side,
            max_side,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return img, (orig_w, orig_h)


def load_scaled_image(path: str, max_side: int) -> tuple[QImage, tuple[int, int]]:
    """Decode an SVG or raster image bounded to max_side and return (image, (orig_w, orig_h))."""
    if not path or not Path(path).is_file():
        return QImage(), (0, 0)
    bounded = max(1, max_side)
    if Path(path).suffix.lower() == ".svg":
        return _load_svg_image(path, bounded)
    return _load_raster_image(path, bounded)


def _run_ffmpeg_frame(ffmpeg: str, path: str, timestamp: float) -> bytes:
    cmd = [
        ffmpeg,
        "-nostdin",
        "-ss",
        f"{max(0.0, timestamp):.2f}",
        "-i",
        path,
        "-frames:v",
        "1",
        "-vf",
        "scale=640:-1",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=_FFMPEG_FRAME_TIMEOUT, check=False)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return b""
    return proc.stdout if proc.returncode == 0 else b""


def extract_video_frame_cached(path: str, timestamp: float) -> QImage:
    """Extract a thread-safe QImage video frame at timestamp with disk caching in THUMBS_CACHE_DIR."""
    if not path or not Path(path).is_file():
        return QImage()

    cache_path = _thumb_cache_path(path, Path(path).stat().st_mtime, timestamp)
    if cache_path.is_file():
        cached = QImage(str(cache_path))
        if not cached.isNull():
            return cached

    ffmpeg = shutil.which("ffmpeg")
    raw_png = _run_ffmpeg_frame(ffmpeg, path, timestamp) if ffmpeg is not None else b""
    if not raw_png:
        return QImage()

    img = QImage()
    if not img.loadFromData(raw_png, "PNG") or img.isNull():
        return QImage()

    _write_thumb_cache(cache_path, raw_png)
    return img


def _run_ffmpeg_pcm(ffmpeg: str, path: str) -> bytes:
    cmd = [
        ffmpeg,
        "-nostdin",
        "-loglevel",
        "quiet",
        "-i",
        path,
        "-t",
        _WAVEFORM_MAX_SECONDS,
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(_WAVEFORM_SAMPLE_RATE),
        "-f",
        "s16le",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=_FFMPEG_WAVEFORM_TIMEOUT, check=False)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return b""
    return proc.stdout if proc.returncode == 0 else b""


def extract_waveform_bars(path: str, bar_count: int = 64) -> list[float]:
    """Compute normalized RMS amplitude bars for an audio file via ffmpeg."""
    if bar_count <= 0:
        return []

    ffmpeg = shutil.which("ffmpeg") if path and Path(path).is_file() else None
    raw_pcm = _run_ffmpeg_pcm(ffmpeg, path) if ffmpeg is not None else b""
    if len(raw_pcm) < _MIN_AUDIO_BYTES:
        return _default_waveform_bars(bar_count)

    even_len = (len(raw_pcm) // 2) * 2
    samples = np.frombuffer(raw_pcm[:even_len], dtype=np.int16)
    floats = samples.astype(np.float32) / 32768.0
    chunks = np.array_split(floats, bar_count)
    rms_values = [float(np.sqrt(np.mean(chunk * chunk))) if chunk.size > 0 else 0.0 for chunk in chunks]
    peak = max(rms_values, default=0.0)
    if peak <= 0.0:
        return _default_waveform_bars(bar_count)
    return [max(_MIN_WAVEFORM_BAR, min(1.0, val / peak)) for val in rms_values]


def read_preview_text(path: str, excerpt: str = "", max_bytes: int = MAX_PREVIEW_TEXT_BYTES) -> str:
    """Read document or text content for preview, truncating oversized files at max_bytes."""
    if not path:
        return excerpt
    target = Path(path)
    if not target.is_file():
        return excerpt

    if target.suffix.lower() in _DOC_EXTRACT_SUFFIXES:
        try:
            text = read_doc_text(path)
        except (OSError, ValueError, zipfile.BadZipFile, KeyError, ET.ParseError):
            return excerpt
        return f"{text[:max_bytes]}\n…" if len(text) > max_bytes else text

    try:
        with target.open("rb") as fh:
            raw = fh.read(max_bytes + 1)
    except OSError:
        return excerpt

    truncated = len(raw) > max_bytes
    text = raw[:max_bytes].decode("utf-8", errors="replace")
    return f"{text}\n…" if truncated else text


class PreviewSignals(QObject):
    """Signal carrier emitting completed preview task results to the GUI thread."""

    finished = Signal(int, object)


class PreviewTask(QRunnable):
    """Runnable executing a pure preview I/O task off the GUI thread."""

    def __init__(self, request_id: int, fn: Callable[[], object]) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self.request_id = request_id
        self._fn = fn
        self.signals = PreviewSignals()

    def run(self) -> None:
        """Run task callable and emit result tagged with request_id."""
        result = self._fn()
        self.signals.finished.emit(self.request_id, result)


class PreviewLoader(QObject):
    """Dedicated QThreadPool preview coordinator discarding stale selection results."""

    def __init__(self, parent: QObject | None = None, *, max_threads: int = 2) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max_threads)
        self._next_id = 0
        self._active_ids: dict[str, int] = {}
        self._callbacks: dict[int, tuple[str, Callable[[object], None], PreviewSignals]] = {}

    def cancel(self, channel: str | None = None) -> None:
        """Invalidate pending callbacks for a single channel or all channels."""
        if channel is None:
            self._active_ids.clear()
            self._callbacks.clear()
            self._pool.clear()
            return
        self._active_ids.pop(channel, None)
        stale_ids = [rid for rid, (ch, _, _) in self._callbacks.items() if ch == channel]
        for rid in stale_ids:
            self._callbacks.pop(rid, None)

    def submit(self, channel: str, fn: Callable[[], object], on_ready: Callable[[object], None]) -> int:
        """Submit a background preview task superseding previous requests on the same channel."""
        self._next_id += 1
        req_id = self._next_id
        self._active_ids[channel] = req_id

        task = PreviewTask(req_id, fn)
        self._callbacks[req_id] = (channel, on_ready, task.signals)
        task.signals.finished.connect(self._on_task_finished, Qt.ConnectionType.QueuedConnection)
        self._pool.start(task)
        return req_id

    def _on_task_finished(self, request_id: int, result: object) -> None:
        entry = self._callbacks.pop(request_id, None)
        if entry is None:
            return
        channel, on_ready, _signals = entry
        if self._active_ids.get(channel) != request_id:
            return
        on_ready(result)

    def shutdown(self) -> None:
        """Cancel queued tasks and wait for active preview workers to exit."""
        self.cancel()
        self._pool.waitForDone(3000)
