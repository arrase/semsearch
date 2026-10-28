"""Multimodal preview pane with specialized visual renderers for desktop search results."""

import math
import subprocess
from datetime import datetime
from pathlib import Path

import pymupdf
from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QResizeEvent,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from semsearch.core.search_text import extract_terms
from semsearch.core.types import FileKind, SearchHit
from semsearch.extractors.docs import read_doc_text
from semsearch.gui.theme import (
    ThemeColors,
    ThemeLayout,
    get_mono_font,
    get_ui_font,
)
from semsearch.gui.widgets import ElidedLabel
from semsearch.i18n import tr
from semsearch.services.actions import open_file, open_media_at, reveal_in_file_manager


def format_file_size(size_bytes: int) -> str:
    """Format byte size into human readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def format_tilde_path(path_str: str) -> str:
    """Convert home directory prefix into tilde notation."""
    home = str(Path.home())
    if path_str.startswith(home):
        return "~" + path_str[len(home) :]
    return path_str


def _extract_video_frame(path: str, timestamp: float) -> QPixmap:
    """Extract a single video frame at timestamp via ffmpeg when available."""
    if not path or not Path(path).is_file():
        return QPixmap()
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
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
            ],
            capture_output=True,
            timeout=2.5,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            pix = QPixmap()
            if pix.loadFromData(proc.stdout, "PNG") and not pix.isNull():
                return pix
    except (OSError, subprocess.SubprocessError):
        return QPixmap()
    return QPixmap()


class MetadataCard(QFrame):
    """Compact metadata display showing file details, timestamp, match reason, and file actions."""

    action_triggered = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._path: str = ""
        self.setObjectName("metadataCard")
        self.setStyleSheet(
            f"QFrame#metadataCard {{ background-color: {ThemeColors.BG_CARD}; "
            f"border: 1px solid {ThemeColors.BORDER}; border-radius: 8px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        self.name_label = ElidedLabel()
        self.name_label.setFont(get_ui_font(12, QFont.Weight.DemiBold))
        self.name_label.setStyleSheet(f"color: {ThemeColors.TEXT_PRIMARY}; border: none; background: transparent;")

        self.reason_badge = QLabel()
        self.reason_badge.setFont(get_ui_font(10, QFont.Weight.Medium))
        self.reason_badge.setStyleSheet(
            f"background-color: rgba(99, 102, 241, 0.2); color: {ThemeColors.BRAND_START};"
            "border: 1px solid rgba(99, 102, 241, 0.4); border-radius: 4px; padding: 2px 6px;"
        )

        top_row.addWidget(self.name_label, stretch=1)
        top_row.addWidget(self.reason_badge)

        self.path_label = ElidedLabel()
        self.path_label.setFont(get_ui_font(11))
        self.path_label.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY}; border: none; background: transparent;")

        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 2, 0, 0)
        info_row.setSpacing(6)

        self.meta_label = QLabel()
        self.meta_label.setFont(get_ui_font(11))
        self.meta_label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; border: none; background: transparent;")

        btn_style = (
            f"QPushButton {{ background-color: rgba(255, 255, 255, 0.06); color: {ThemeColors.TEXT_SECONDARY}; "
            f"border: 1px solid {ThemeColors.BORDER}; border-radius: 5px; padding: 2px 8px; font-size: 11px; }}"
            f"QPushButton:hover {{ background-color: rgba(99, 102, 241, 0.2); color: {ThemeColors.TEXT_PRIMARY}; "
            f"border-color: {ThemeColors.BRAND_START}; }}"
        )
        self.open_btn = QPushButton(tr("Open"))
        self.open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_btn.setStyleSheet(btn_style)
        self.open_btn.clicked.connect(self._on_open_clicked)

        self.reveal_btn = QPushButton(tr("Show in Files"))
        self.reveal_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reveal_btn.setStyleSheet(btn_style)
        self.reveal_btn.clicked.connect(self._on_reveal_clicked)

        info_row.addWidget(self.meta_label)
        info_row.addStretch()
        info_row.addWidget(self.open_btn)
        info_row.addWidget(self.reveal_btn)

        layout.addLayout(top_row)
        layout.addWidget(self.path_label)
        layout.addLayout(info_row)

    def retranslate_ui(self) -> None:
        """Refresh translated button labels."""
        self.open_btn.setText(tr("Open"))
        self.reveal_btn.setText(tr("Show in Files"))

    def update_metadata(
        self,
        name: str,
        path: str,
        size_bytes: int = 0,
        mtime: float = 0.0,
        reason: str = "",
    ) -> None:
        """Update displayed metadata fields."""
        self._path = path
        self.name_label.setText(name)
        self.path_label.setText(format_tilde_path(path))

        if size_bytes <= 0 and mtime <= 0 and path and Path(path).exists():
            stat = Path(path).stat()
            size_bytes = stat.st_size
            mtime = stat.st_mtime

        meta_parts: list[str] = []
        if size_bytes > 0:
            meta_parts.append(format_file_size(size_bytes))
        if mtime > 0:
            dt = datetime.fromtimestamp(mtime)
            meta_parts.append(dt.strftime("%Y-%m-%d %H:%M"))
        self.meta_label.setText(" • ".join(meta_parts) if meta_parts else tr("Local file"))

        display_reason = reason if reason else tr("Matches query")
        self.reason_badge.setText(display_reason)

    def _on_open_clicked(self) -> None:
        if self._path:
            open_file(self._path)
            self.action_triggered.emit()

    def _on_reveal_clicked(self) -> None:
        if self._path:
            reveal_in_file_manager(self._path)
            self.action_triggered.emit()


class EmptyPreviewWidget(QWidget):
    """Empty placeholder widget when no result is selected."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_label = QLabel("🔍")
        self.icon_label.setFont(get_ui_font(32))
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.text_label = QLabel(tr("Select an item to preview"))
        self.text_label.setFont(get_ui_font(13))
        self.text_label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED};")
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label)

    def retranslate_ui(self) -> None:
        """Refresh translated placeholder label."""
        self.text_label.setText(tr("Select an item to preview"))


class ImagePreviewWidget(QWidget):
    """Image and screenshot preview renderer with aspect-ratio preservation."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(200, 160)
        self.image_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.image_label.setStyleSheet("border-radius: 8px; background-color: rgba(0, 0, 0, 0.2);")

        self.dimensions_label = QLabel()
        self.dimensions_label.setFont(get_ui_font(11))
        self.dimensions_label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED};")
        self.dimensions_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.meta_card = MetadataCard()

        layout.addWidget(self.image_label, stretch=1)
        layout.addWidget(self.dimensions_label)
        layout.addWidget(self.meta_card)

        self._pixmap = QPixmap()

    def retranslate_ui(self) -> None:
        """Refresh translated labels."""
        self.meta_card.retranslate_ui()
        if self._pixmap.isNull() and self.image_label.text():
            self.image_label.setText(tr("Unable to load image"))

    def load_image(self, path: str, name: str, size: int = 0, mtime: float = 0.0, reason: str = "") -> None:
        """Load and display image preserving aspect ratio."""
        self._pixmap = QPixmap(path)
        if not self._pixmap.isNull():
            self.dimensions_label.setText(f"{self._pixmap.width()} × {self._pixmap.height()} px")
            self._rescale_pixmap()
        else:
            self.image_label.clear()
            self.image_label.setText(tr("Unable to load image"))
            self.dimensions_label.setText("")

        self.meta_card.update_metadata(name, path, size, mtime, reason)

    def _rescale_pixmap(self) -> None:
        if self._pixmap.isNull():
            return
        target = self.image_label.size() - QSize(12, 12)
        scaled = self._pixmap.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._rescale_pixmap()

    def clear(self) -> None:
        """Release cached image pixmap and clear labels."""
        self._pixmap = QPixmap()
        self.image_label.clear()
        self.dimensions_label.clear()


class PdfPreviewWidget(QWidget):
    """PDF page renderer with PyMuPDF and yellow search term highlights."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.page_label = QLabel()
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setMinimumSize(200, 160)
        self.page_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.page_label.setStyleSheet("border-radius: 8px; background-color: rgba(255, 255, 255, 0.04);")

        nav_row = QHBoxLayout()
        self.prev_btn = QPushButton(tr("◀ Prev"))
        self.prev_btn.clicked.connect(self._prev_page)
        self.page_info = QLabel(tr("Page {current} of {total}", current=1, total=1))
        self.page_info.setFont(get_ui_font(11))
        self.page_info.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY};")
        self.page_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next_btn = QPushButton(tr("Next ▶"))
        self.next_btn.clicked.connect(self._next_page)

        nav_row.addWidget(self.prev_btn)
        nav_row.addWidget(self.page_info, stretch=1)
        nav_row.addWidget(self.next_btn)

        self.meta_card = MetadataCard()

        layout.addWidget(self.page_label, stretch=1)
        layout.addLayout(nav_row)
        layout.addWidget(self.meta_card)

        self._doc: pymupdf.Document | None = None
        self._current_page: int = 0
        self._total_pages: int = 0
        self._query_terms: list[str] = []
        self._rendered_pixmap = QPixmap()

    def retranslate_ui(self) -> None:
        """Refresh translated navigation buttons and page info."""
        self.prev_btn.setText(tr("◀ Prev"))
        self.next_btn.setText(tr("Next ▶"))
        if self._total_pages > 0:
            self.page_info.setText(
                tr("Page {current} of {total}", current=self._current_page + 1, total=self._total_pages)
            )
        else:
            self.page_info.setText(tr("Page {current} of {total}", current=1, total=1))
        self.meta_card.retranslate_ui()

    def load_pdf(
        self,
        path: str,
        name: str,
        page_number: int = 1,
        query: str = "",
        size: int = 0,
        mtime: float = 0.0,
        reason: str = "",
    ) -> None:
        """Load target PDF page and highlight query keywords in yellow."""
        self.meta_card.update_metadata(name, path, size, mtime, reason)
        if self._doc is not None:
            self._doc.close()
            self._doc = None

        if not Path(path).is_file():
            self._rendered_pixmap = QPixmap()
            self.page_label.clear()
            self.page_label.setText(tr("File not found: {name}", name=name))
            self.page_info.setText(tr("Page {current} of {total}", current=0, total=0))
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            return

        terms = extract_terms(query)
        self._query_terms = terms if terms else [t for t in query.lower().split() if len(t) > 1]
        self._doc = pymupdf.open(path)
        self._total_pages = len(self._doc)
        self._current_page = max(0, min(page_number - 1, self._total_pages - 1))
        self._render_current_page()

    def _render_current_page(self) -> None:
        if self._doc is None or self._total_pages == 0:
            return

        page = self._doc[self._current_page]
        zoom = 1.6
        mat = pymupdf.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)

        image = QImage(
            pix.samples,
            pix.width,
            pix.height,
            pix.stride,
            QImage.Format.Format_RGB888,
        )
        qpix = QPixmap.fromImage(image.copy())

        painter = QPainter(qpix)
        highlight_brush = QBrush(QColor(250, 204, 21, 102))
        highlight_pen = QPen(QColor(250, 204, 21, 204), 1.5)

        for term in self._query_terms:
            rects = page.search_for(term)
            for r in rects:
                box = QRectF(r.x0 * zoom, r.y0 * zoom, (r.x1 - r.x0) * zoom, (r.y1 - r.y0) * zoom)
                painter.fillRect(box, highlight_brush)
                painter.setPen(highlight_pen)
                painter.drawRect(box)

        painter.end()

        self._rendered_pixmap = qpix
        self._rescale_page()
        self.page_info.setText(tr("Page {current} of {total}", current=self._current_page + 1, total=self._total_pages))
        self.prev_btn.setEnabled(self._current_page > 0)
        self.next_btn.setEnabled(self._current_page < self._total_pages - 1)

    def _rescale_page(self) -> None:
        if self._rendered_pixmap.isNull():
            return
        target = self.page_label.size() - QSize(12, 12)
        scaled = self._rendered_pixmap.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.page_label.setPixmap(scaled)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._rescale_page()

    def _prev_page(self) -> None:
        if self._current_page > 0:
            self._current_page -= 1
            self._render_current_page()

    def _next_page(self) -> None:
        if self._current_page < self._total_pages - 1:
            self._current_page += 1
            self._render_current_page()

    def clear(self) -> None:
        """Close open PDF document and release rendered page pixmap."""
        if self._doc is not None:
            self._doc.close()
            self._doc = None
        self._rendered_pixmap = QPixmap()
        self.page_label.clear()
        self._total_pages = 0
        self._current_page = 0
        self._query_terms = []


class WaveformWidget(QWidget):
    """Synthetic 64-bar audio waveform visualization with scrub position."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(80)
        self._progress: float = 0.0
        self._bars: list[float] = [abs(math.sin(i * 0.28) * math.cos(i * 0.14)) * 0.75 + 0.15 for i in range(64)]

    def set_progress(self, progress: float) -> None:
        """Update active progress in [0.0, 1.0]."""
        self._progress = max(0.0, min(progress, 1.0))
        self.update()

    def set_seed(self, path: str) -> None:
        """Generate deterministic bar amplitudes from file path."""
        seed = sum(ord(c) for c in path)
        self._bars = [
            abs(math.sin((i + seed) * 0.28) * math.cos((i + seed * 2) * 0.14)) * 0.75 + 0.15 for i in range(64)
        ]
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width = self.width()
        height = self.height()
        bar_count = 64
        gap = 2.0
        bar_width = max(1.0, (width - (bar_count - 1) * gap) / bar_count)

        active_count = int(self._progress * bar_count)

        for i in range(bar_count):
            bar_h = self._bars[i] * height
            x = i * (bar_width + gap)
            y = (height - bar_h) / 2.0

            color = QColor(ThemeColors.BRAND_START) if i <= active_count else QColor(ThemeColors.TEXT_MUTED)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawRoundedRect(QRectF(x, y, bar_width, bar_h), 1.5, 1.5)

        painter.end()


class AudioPreviewWidget(QWidget):
    """Audio player preview with synthetic waveform visualization and scrub bar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        self.waveform = WaveformWidget()

        controls = QHBoxLayout()
        self.play_btn = QPushButton(tr("▶ Play"))
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.clicked.connect(self._toggle_playback)

        self.scrub_slider = QSlider(Qt.Orientation.Horizontal)
        self.scrub_slider.setRange(0, 1000)
        self.scrub_slider.valueChanged.connect(self._on_scrub)

        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setFont(get_ui_font(11))
        self.time_label.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY};")

        controls.addWidget(self.play_btn)
        controls.addWidget(self.scrub_slider, stretch=1)
        controls.addWidget(self.time_label)

        self.meta_card = MetadataCard()

        layout.addWidget(self.waveform, stretch=1)
        layout.addLayout(controls)
        layout.addWidget(self.meta_card)

        self._path: str = ""
        self._duration: float = 180.0
        self._current_pos: float = 0.0

    def retranslate_ui(self) -> None:
        """Refresh translated button label."""
        self.play_btn.setText(tr("▶ Play"))
        self.meta_card.retranslate_ui()

    def load_audio(
        self,
        path: str,
        name: str,
        loc: float = 0.0,
        size: int = 0,
        mtime: float = 0.0,
        reason: str = "",
    ) -> None:
        """Load audio result and position scrubber at hit moment."""
        self._path = path
        self._current_pos = loc
        self._duration = max(loc * 1.5, 120.0)
        self.waveform.set_seed(path)

        pos_ratio = self._current_pos / self._duration if self._duration > 0 else 0.0
        self.scrub_slider.setValue(int(pos_ratio * 1000))
        self.waveform.set_progress(pos_ratio)
        self._update_time_label()

        self.meta_card.update_metadata(name, path, size, mtime, reason)

    def _toggle_playback(self) -> None:
        if self._path:
            open_media_at(self._path, self._current_pos)

    def _on_scrub(self, val: int) -> None:
        ratio = val / 1000.0
        self._current_pos = ratio * self._duration
        self.waveform.set_progress(ratio)
        self._update_time_label()

    def _update_time_label(self) -> None:
        cur_min, cur_sec = divmod(int(self._current_pos), 60)
        dur_min, dur_sec = divmod(int(self._duration), 60)
        self.time_label.setText(f"{cur_min}:{cur_sec:02d} / {dur_min}:{dur_sec:02d}")

    def clear(self) -> None:
        """Reset audio preview state."""
        self._path = ""
        self._current_pos = 0.0


class VideoPreviewWidget(QWidget):
    """Video preview with real ffmpeg frame thumbnail, scrub bar, and timestamp jumping."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.poster_label = QLabel()
        self.poster_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.poster_label.setMinimumSize(200, 160)
        self.poster_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.poster_label.setStyleSheet("border-radius: 8px; background-color: rgba(0, 0, 0, 0.4);")

        controls = QHBoxLayout()
        self.scrub_slider = QSlider(Qt.Orientation.Horizontal)
        self.scrub_slider.setRange(0, 1000)
        self.scrub_slider.valueChanged.connect(self._on_scrub)

        self.jump_btn = QPushButton(tr("▶ Open at Moment"))
        self.jump_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.jump_btn.clicked.connect(self._jump_to_timestamp)

        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setFont(get_ui_font(11))
        self.time_label.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY};")

        controls.addWidget(self.scrub_slider, stretch=1)
        controls.addWidget(self.time_label)
        controls.addWidget(self.jump_btn)

        self.meta_card = MetadataCard()

        layout.addWidget(self.poster_label, stretch=1)
        layout.addLayout(controls)
        layout.addWidget(self.meta_card)

        self._path: str = ""
        self._current_pos: float = 0.0
        self._duration: float = 300.0
        self._poster_pixmap = QPixmap()

    def retranslate_ui(self) -> None:
        """Refresh translated button label."""
        self.jump_btn.setText(tr("▶ Open at Moment"))
        self.meta_card.retranslate_ui()

    def load_video(
        self,
        path: str,
        name: str,
        loc: float = 0.0,
        size: int = 0,
        mtime: float = 0.0,
        reason: str = "",
    ) -> None:
        """Load video metadata, extract frame at loc, and seek to match position."""
        self._path = path
        self._current_pos = loc
        self._duration = max(loc * 1.5, 300.0)

        frame_pix = _extract_video_frame(path, loc)
        if not frame_pix.isNull():
            pixmap = frame_pix.scaled(
                QSize(480, 270),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        else:
            pixmap = QPixmap(390, 220)
            pixmap.fill(QColor("#1A1D24"))

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        center_x = pixmap.width() / 2.0
        center_y = pixmap.height() / 2.0

        painter.setPen(Qt.PenStyle.NoPen)
        badge_color = QColor(ThemeColors.BRAND_START)
        badge_color.setAlpha(215)
        painter.setBrush(QBrush(badge_color))
        painter.drawEllipse(QRectF(center_x - 28, center_y - 28, 56, 56))

        painter.setBrush(QBrush(QColor("#FFFFFF")))
        triangle = [
            QPoint(int(center_x - 7), int(center_y - 13)),
            QPoint(int(center_x + 13), int(center_y)),
            QPoint(int(center_x - 7), int(center_y + 13)),
        ]
        painter.drawPolygon(triangle)
        painter.end()

        self._poster_pixmap = pixmap
        self._rescale_poster()

        pos_ratio = self._current_pos / self._duration if self._duration > 0 else 0.0
        self.scrub_slider.setValue(int(pos_ratio * 1000))
        self._update_time_label()

        self.meta_card.update_metadata(name, path, size, mtime, reason)

    def _rescale_poster(self) -> None:
        if self._poster_pixmap.isNull():
            return
        target = self.poster_label.size() - QSize(12, 12)
        scaled = self._poster_pixmap.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.poster_label.setPixmap(scaled)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._rescale_poster()

    def _on_scrub(self, val: int) -> None:
        ratio = val / 1000.0
        self._current_pos = ratio * self._duration
        self._update_time_label()

    def _jump_to_timestamp(self) -> None:
        if self._path:
            open_media_at(self._path, self._current_pos)

    def _update_time_label(self) -> None:
        cur_min, cur_sec = divmod(int(self._current_pos), 60)
        dur_min, dur_sec = divmod(int(self._duration), 60)
        self.time_label.setText(f"{cur_min}:{cur_sec:02d} / {dur_min}:{dur_sec:02d}")

    def clear(self) -> None:
        """Release video poster pixmap and reset state."""
        self._poster_pixmap = QPixmap()
        self.poster_label.clear()
        self._path = ""
        self._current_pos = 0.0


class LineNumberGutter(QWidget):
    """Line number gutter for code preview widget."""

    def __init__(self, editor: "CodeEditor") -> None:
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event) -> None:
        self.editor.paint_gutter(event)


class CodeEditor(QPlainTextEdit):
    """Read-only code editor with line number gutter and definition highlight."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFont(get_mono_font(12))
        self.gutter = LineNumberGutter(self)

        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self._update_gutter_width()

    def gutter_width(self) -> int:
        digits = max(1, len(str(self.blockCount())))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, event) -> None:
        painter = QPainter(self.gutter)
        painter.fillRect(event.rect(), QColor("#1A1D24"))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())

        painter.setPen(QColor(ThemeColors.TEXT_MUTED))
        painter.setFont(self.font())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number_str = str(block_number + 1)
                painter.drawText(
                    0,
                    top,
                    self.gutter.width() - 6,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight,
                    number_str,
                )
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1

        painter.end()


class CodePreviewWidget(QWidget):
    """Source code and document text preview with gutter and match highlighting."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.editor = CodeEditor()
        self.meta_card = MetadataCard()

        layout.addWidget(self.editor, stretch=1)
        layout.addWidget(self.meta_card)

    def retranslate_ui(self) -> None:
        """Refresh translated metadata card labels."""
        self.meta_card.retranslate_ui()

    def load_code(
        self,
        path: str,
        name: str,
        line: int = 0,
        size: int = 0,
        mtime: float = 0.0,
        reason: str = "",
        query: str = "",
        excerpt: str = "",
        wrap_lines: bool = False,
    ) -> None:
        """Load code or document content and highlight target line and query matches."""
        target_path = Path(path)
        is_doc_file = target_path.suffix.lower() in (".docx", ".odt", ".rtf", ".html", ".htm", ".txt")
        self.editor.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.WidgetWidth
            if (wrap_lines or is_doc_file)
            else QPlainTextEdit.LineWrapMode.NoWrap
        )
        if not target_path.is_file():
            content = excerpt if excerpt else ""
        elif target_path.suffix.lower() in (".docx", ".odt", ".rtf", ".html", ".htm"):
            content = read_doc_text(path)
        else:
            content = target_path.read_text(encoding="utf-8", errors="replace")
        self.editor.setPlainText(content)

        doc = self.editor.document()
        cursor = QTextCursor(doc)
        selections: list[QTextEdit.ExtraSelection] = []

        if line > 0:
            block = doc.findBlockByNumber(line - 1)
            if block.isValid():
                cursor = QTextCursor(block)
            line_selection = QTextEdit.ExtraSelection()
            line_selection.format.setBackground(QColor(250, 204, 21, 90))
            line_selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            line_selection.cursor = cursor
            selections.append(line_selection)

        first_match_cursor: QTextCursor | None = None
        if query:
            terms = extract_terms(query)
            if not terms:
                terms = [t for t in query.lower().split() if len(t) > 1]
            for term in terms:
                match_cursor = QTextCursor(doc)
                while True:
                    match_cursor = doc.find(term, match_cursor)
                    if match_cursor.isNull():
                        break
                    if first_match_cursor is None:
                        first_match_cursor = QTextCursor(match_cursor)
                    term_sel = QTextEdit.ExtraSelection()
                    term_sel.format.setBackground(QColor(250, 204, 21, 140))
                    term_sel.format.setForeground(QColor("#0F1115"))
                    term_sel.cursor = match_cursor
                    selections.append(term_sel)

        if line <= 0:
            if first_match_cursor is not None:
                cursor = first_match_cursor
            elif excerpt:
                clean_excerpt = excerpt.replace("«", "").replace("»", "").replace("…", "").strip()
                first_line_snip = clean_excerpt.splitlines()[0].strip()[:60] if clean_excerpt else ""
                if first_line_snip:
                    ex_cursor = doc.find(first_line_snip)
                    if not ex_cursor.isNull():
                        cursor = ex_cursor

        self.editor.setExtraSelections(selections)
        self.editor.setTextCursor(cursor)
        self.editor.centerCursor()

        self.meta_card.update_metadata(name, path, size, mtime, reason)

    def clear(self) -> None:
        """Clear editor text and extra selections."""
        self.editor.clear()
        self.editor.setExtraSelections([])


class PreviewPane(QStackedWidget):
    """Stacked multimodal preview pane coordinating all media viewers."""

    action_triggered = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(360)
        self.resize(ThemeLayout.PREVIEW_WIDTH, 420)

        self.empty_view = EmptyPreviewWidget()
        self.image_view = ImagePreviewWidget()
        self.pdf_view = PdfPreviewWidget()
        self.audio_view = AudioPreviewWidget()
        self.video_view = VideoPreviewWidget()
        self.code_view = CodePreviewWidget()

        for view in (
            self.image_view,
            self.pdf_view,
            self.audio_view,
            self.video_view,
            self.code_view,
        ):
            view.meta_card.action_triggered.connect(self.action_triggered)

        self.addWidget(self.empty_view)  # 0
        self.addWidget(self.image_view)  # 1
        self.addWidget(self.pdf_view)  # 2
        self.addWidget(self.audio_view)  # 3
        self.addWidget(self.video_view)  # 4
        self.addWidget(self.code_view)  # 5

        self.setCurrentIndex(0)

    def load_result(self, item: SearchHit, query: str = "") -> None:
        """Route item to appropriate multimodal preview renderer."""
        path = item.path
        name = Path(item.path).name
        kind = item.kind
        loc = item.loc
        focus = int(item.focus) if item.focus > 0 else int(item.loc)
        size = item.size
        mtime = item.modified
        reason = item.reason

        clean_query = query.strip()
        if clean_query.lower().startswith("code:"):
            clean_query = clean_query[5:].strip()

        suffix = Path(path).suffix.lower()

        if kind in (FileKind.IMAGE, FileKind.SCREENSHOT) or suffix in (
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".bmp",
            ".gif",
        ):
            self.image_view.load_image(path, name, size, mtime, reason)
            self.setCurrentIndex(1)
        elif kind == FileKind.PDF or suffix == ".pdf":
            page = int(loc) if loc > 0 else 1
            self.pdf_view.load_pdf(path, name, page, clean_query, size, mtime, reason)
            self.setCurrentIndex(2)
        elif kind == FileKind.AUDIO or suffix in (".mp3", ".wav", ".ogg", ".flac", ".m4a"):
            self.audio_view.load_audio(path, name, loc, size, mtime, reason)
            self.setCurrentIndex(3)
        elif kind == FileKind.VIDEO or suffix in (".mp4", ".mkv", ".webm", ".avi", ".mov"):
            self.video_view.load_video(path, name, loc, size, mtime, reason)
            self.setCurrentIndex(4)
        elif kind == FileKind.CODE or (
            kind != FileKind.DOC
            and suffix
            in (".py", ".js", ".ts", ".rs", ".go", ".c", ".cpp", ".h", ".json", ".yaml", ".yml", ".toml", ".md", ".sh")
        ):
            self.code_view.load_code(
                path, name, focus, size, mtime, reason, query=clean_query, excerpt=item.excerpt, wrap_lines=False
            )
            self.setCurrentIndex(5)
        else:
            self.code_view.load_code(
                path, name, 0, size, mtime, reason, query=clean_query, excerpt=item.excerpt, wrap_lines=True
            )
            self.setCurrentIndex(5)

    def clear(self) -> None:
        """Reset preview pane to empty placeholder view and release buffered resources."""
        self.image_view.clear()
        self.pdf_view.clear()
        self.audio_view.clear()
        self.video_view.clear()
        self.code_view.clear()
        self.setCurrentIndex(0)

    def retranslate_ui(self) -> None:
        """Refresh translated strings across all child preview views."""
        self.empty_view.retranslate_ui()
        self.image_view.retranslate_ui()
        self.pdf_view.retranslate_ui()
        self.audio_view.retranslate_ui()
        self.video_view.retranslate_ui()
        self.code_view.retranslate_ui()
