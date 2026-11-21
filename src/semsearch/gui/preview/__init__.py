"""Multimodal preview package coordinating specialized visual, media, PDF, and code renderers."""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QStackedWidget, QWidget

from semsearch.config import AppConfig
from semsearch.core.search_text import parse_query_filters
from semsearch.core.types import FileKind, SearchHit
from semsearch.gui.preview.code_view import CodeEditor, CodePreviewWidget
from semsearch.gui.preview.image_view import ImagePreviewWidget
from semsearch.gui.preview.loader import PreviewLoader
from semsearch.gui.preview.media_view import AudioPreviewWidget, VideoPreviewWidget, WaveformWidget
from semsearch.gui.preview.metadata import (
    EmptyPreviewWidget,
    MetadataCard,
    format_file_size,
    format_tilde_path,
)
from semsearch.gui.preview.pdf_view import PdfPreviewWidget
from semsearch.gui.theme import ThemeLayout

__all__ = [
    "AudioPreviewWidget",
    "CodeEditor",
    "CodePreviewWidget",
    "EmptyPreviewWidget",
    "ImagePreviewWidget",
    "MetadataCard",
    "PdfPreviewWidget",
    "PreviewPane",
    "VideoPreviewWidget",
    "WaveformWidget",
    "format_file_size",
    "format_tilde_path",
]

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".svg")
_AUDIO_SUFFIXES = (".mp3", ".wav", ".ogg", ".flac", ".m4a")
_VIDEO_SUFFIXES = (".mp4", ".mkv", ".webm", ".avi", ".mov")
_ASYNC_IMAGE_THRESHOLD_BYTES = 512 * 1024
_ASYNC_TEXT_THRESHOLD_BYTES = 128 * 1024


def _resolve_file_size(path: str, item_size: int) -> int:
    if item_size > 0:
        return item_size
    target = Path(path)
    if path and target.is_file():
        return target.stat().st_size
    return 0


def _extract_duration(info: dict[str, object]) -> float:
    raw = info.get("duration", 0.0)
    return float(raw) if isinstance(raw, (int, float)) else 0.0


class PreviewPane(QStackedWidget):
    """Stacked multimodal preview pane coordinating all media viewers."""

    action_triggered = Signal()

    def __init__(self, config: AppConfig | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._loader = PreviewLoader(self)
        self.setMinimumWidth(360)
        self.resize(ThemeLayout.PREVIEW_WIDTH, 420)

        self.empty_view = EmptyPreviewWidget()
        self.image_view = ImagePreviewWidget(config=config, loader=self._loader)
        self.pdf_view = PdfPreviewWidget(config=config)
        self.audio_view = AudioPreviewWidget(config=config, loader=self._loader)
        self.video_view = VideoPreviewWidget(config=config, loader=self._loader)
        self.code_view = CodePreviewWidget(config=config, loader=self._loader)

        for view in (
            self.image_view,
            self.pdf_view,
            self.audio_view,
            self.video_view,
            self.code_view,
        ):
            view.meta_card.action_triggered.connect(self.action_triggered)
        self.audio_view.action_triggered.connect(self.action_triggered)
        self.video_view.action_triggered.connect(self.action_triggered)

        self.addWidget(self.empty_view)  # 0
        self.addWidget(self.image_view)  # 1
        self.addWidget(self.pdf_view)  # 2
        self.addWidget(self.audio_view)  # 3
        self.addWidget(self.video_view)  # 4
        self.addWidget(self.code_view)  # 5

        self.setCurrentIndex(0)

    def load_result(self, item: SearchHit, query: str = "") -> None:
        """Route item to appropriate multimodal preview renderer."""
        self._loader.cancel()
        self.audio_view.stop_playback()
        self.video_view.stop_playback()

        path = item.path
        name = Path(path).name
        kind = item.kind
        loc = item.loc
        size = item.size
        mtime = item.modified
        reason = str(item.reason)
        clean_query = parse_query_filters(query).clean_query
        suffix = Path(path).suffix.lower()

        if kind in (FileKind.IMAGE, FileKind.SCREENSHOT) or suffix in _IMAGE_SUFFIXES:
            use_async = _resolve_file_size(path, size) > _ASYNC_IMAGE_THRESHOLD_BYTES
            self.image_view.load_image(path, name, size, mtime, reason, hit=item, async_load=use_async)
            self.setCurrentIndex(1)
        elif kind == FileKind.PDF or suffix == ".pdf":
            page = int(loc) if loc > 0 else 1
            self.pdf_view.load_pdf(path, name, page, clean_query, size=size, mtime=mtime, reason=reason, hit=item)
            self.setCurrentIndex(2)
        elif kind == FileKind.AUDIO or suffix in _AUDIO_SUFFIXES:
            duration = _extract_duration(item.info)
            self.audio_view.load_audio(
                path, name, loc, duration=duration, size=size, mtime=mtime, reason=reason, hit=item
            )
            self.setCurrentIndex(3)
        elif kind == FileKind.VIDEO or suffix in _VIDEO_SUFFIXES:
            duration = _extract_duration(item.info)
            self.video_view.load_video(
                path, name, loc, duration=duration, size=size, mtime=mtime, reason=reason, hit=item
            )
            self.setCurrentIndex(4)
        else:
            is_code_view = kind == FileKind.CODE
            focus = int(item.focus) if item.focus > 0 else int(loc)
            target_line = focus if is_code_view else 0
            use_async = _resolve_file_size(path, size) > _ASYNC_TEXT_THRESHOLD_BYTES
            self.code_view.load_code(
                path,
                name,
                target_line,
                size=size,
                mtime=mtime,
                reason=reason,
                query=clean_query,
                excerpt=item.excerpt,
                wrap_lines=not is_code_view,
                hit=item,
                async_load=use_async,
            )
            self.setCurrentIndex(5)

    def clear(self) -> None:
        """Stop playback, cancel async tasks, release buffered resources, and show empty view."""
        self._loader.cancel()
        self.image_view.clear()
        self.pdf_view.clear()
        self.audio_view.clear()
        self.video_view.clear()
        self.code_view.clear()
        self.setCurrentIndex(0)

    def shutdown(self) -> None:
        """Release all preview resources and wait for background worker threads to finish."""
        self.clear()
        self._loader.shutdown()

    def retranslate_ui(self) -> None:
        """Refresh translated strings across all child preview views."""
        self.empty_view.retranslate_ui()
        self.image_view.retranslate_ui()
        self.pdf_view.retranslate_ui()
        self.audio_view.retranslate_ui()
        self.video_view.retranslate_ui()
        self.code_view.retranslate_ui()
