"""Multimodal preview pane with specialized visual renderers for desktop search results."""

from datetime import datetime
import math
from pathlib import Path
import pymupdf
from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
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
    QSlider,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from semsearch.core.types import FileKind, ResultRow, SearchHit
from semsearch.gui.theme import (
    ThemeColors,
    ThemeLayout,
    get_mono_font,
    get_ui_font,
)
from semsearch.services.actions import open_media_timestamp


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
        return "~" + path_str[len(home):]
    return path_str


class MetadataCard(QFrame):
    """Compact metadata display showing file details, timestamp, and match reason."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"background-color: {ThemeColors.BG_CARD};"
            f"border: 1px solid {ThemeColors.BORDER};"
            "border-radius: 8px; padding: 6px 10px;"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self.name_label = QLabel()
        self.name_label.setFont(get_ui_font(13, QFont.Weight.DemiBold))
        self.name_label.setStyleSheet(f"color: {ThemeColors.TEXT_PRIMARY}; border: none; background: transparent;")

        self.path_label = QLabel()
        self.path_label.setFont(get_ui_font(11))
        self.path_label.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY}; border: none; background: transparent;")

        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 0, 0, 0)
        self.meta_label = QLabel()
        self.meta_label.setFont(get_ui_font(11))
        self.meta_label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; border: none; background: transparent;")

        self.reason_badge = QLabel()
        self.reason_badge.setFont(get_ui_font(11, QFont.Weight.Medium))
        self.reason_badge.setStyleSheet(
            f"background-color: rgba(99, 102, 241, 0.2); color: {ThemeColors.BRAND_START};"
            "border: 1px solid rgba(99, 102, 241, 0.4); border-radius: 4px; padding: 2px 6px;"
        )

        info_row.addWidget(self.meta_label)
        info_row.addStretch()
        info_row.addWidget(self.reason_badge)

        layout.addWidget(self.name_label)
        layout.addWidget(self.path_label)
        layout.addLayout(info_row)

    def update_metadata(
        self,
        name: str,
        path: str,
        size_bytes: int = 0,
        mtime: float = 0.0,
        reason: str = "",
    ) -> None:
        """Update displayed metadata fields."""
        self.name_label.setText(name)
        self.path_label.setText(format_tilde_path(path))

        meta_parts: list[str] = []
        if size_bytes > 0:
            meta_parts.append(format_file_size(size_bytes))
        if mtime > 0:
            dt = datetime.fromtimestamp(mtime)
            meta_parts.append(dt.strftime("%Y-%m-%d %H:%M"))
        self.meta_label.setText(" • ".join(meta_parts) if meta_parts else "Local file")

        display_reason = reason if reason else "Matches query"
        self.reason_badge.setText(display_reason)


class EmptyPreviewWidget(QWidget):
    """Empty placeholder widget when no result is selected."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_label = QLabel("🔍")
        self.icon_label.setFont(get_ui_font(32))
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.text_label = QLabel("Select an item to preview")
        self.text_label.setFont(get_ui_font(13))
        self.text_label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED};")
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label)


class ImagePreviewWidget(QWidget):
    """Image and screenshot preview renderer with aspect-ratio preservation."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
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

    def load_image(self, path: str, name: str, size: int = 0, mtime: float = 0.0, reason: str = "") -> None:
        """Load and display image preserving aspect ratio."""
        self._pixmap = QPixmap(path)
        if not self._pixmap.isNull():
            self.dimensions_label.setText(f"{self._pixmap.width()} × {self._pixmap.height()} px")
            scaled = self._pixmap.scaled(
                QSize(390, 260),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.image_label.setPixmap(scaled)
        else:
            self.image_label.setText("Unable to load image")
            self.dimensions_label.setText("")

        self.meta_card.update_metadata(name, path, size, mtime, reason)


class PdfPreviewWidget(QWidget):
    """PDF page renderer with PyMuPDF and yellow search term highlights."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.page_label = QLabel()
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setStyleSheet("border-radius: 8px; background-color: rgba(255, 255, 255, 0.04);")

        nav_row = QHBoxLayout()
        self.prev_btn = QPushButton("◀ Prev")
        self.prev_btn.clicked.connect(self._prev_page)
        self.page_info = QLabel("Page 1 of 1")
        self.page_info.setFont(get_ui_font(11))
        self.page_info.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY};")
        self.page_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next_btn = QPushButton("Next ▶")
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
        if not Path(path).is_file():
            self.page_label.setText(f"File not found: {name}")
            self.page_info.setText("Page 0 of 0")
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            return

        self._query_terms = [t for t in query.lower().split() if len(t) > 2]
        self._doc = pymupdf.open(path)
        self._total_pages = len(self._doc)
        self._current_page = max(0, min(page_number - 1, self._total_pages - 1))
        self._render_current_page()

    def _render_current_page(self) -> None:
        if self._doc is None or self._total_pages == 0:
            return

        page = self._doc[self._current_page]
        zoom = 1.5
        mat = pymupdf.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)

        image = QImage(
            pix.samples,
            pix.width,
            pix.height,
            pix.stride,
            QImage.Format.Format_RGB888,
        )
        qpix = QPixmap.fromImage(image)

        # Highlight search terms in yellow (rgba(250, 204, 21, 0.4))
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

        scaled = qpix.scaled(
            QSize(390, 240),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.page_label.setPixmap(scaled)
        self.page_info.setText(f"Page {self._current_page + 1} of {self._total_pages}")
        self.prev_btn.setEnabled(self._current_page > 0)
        self.next_btn.setEnabled(self._current_page < self._total_pages - 1)

    def _prev_page(self) -> None:
        if self._current_page > 0:
            self._current_page -= 1
            self._render_current_page()

    def _next_page(self) -> None:
        if self._current_page < self._total_pages - 1:
            self._current_page += 1
            self._render_current_page()


class WaveformWidget(QWidget):
    """Synthetic 64-bar audio waveform visualization with scrub position."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(80)
        self._progress: float = 0.0
        self._bars: list[float] = [
            abs(math.sin(i * 0.28) * math.cos(i * 0.14)) * 0.75 + 0.15
            for i in range(64)
        ]

    def set_progress(self, progress: float) -> None:
        """Update active progress in [0.0, 1.0]."""
        self._progress = max(0.0, min(progress, 1.0))
        self.update()

    def set_seed(self, path: str) -> None:
        """Generate deterministic bar amplitudes from file path."""
        seed = sum(ord(c) for c in path)
        self._bars = [
            abs(math.sin((i + seed) * 0.28) * math.cos((i + seed * 2) * 0.14)) * 0.75 + 0.15
            for i in range(64)
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
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedWidth(38)
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
        self._is_playing: bool = False

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
        self._is_playing = not self._is_playing
        self.play_btn.setText("⏸" if self._is_playing else "▶")
        if self._is_playing:
            open_media_timestamp(self._path, self._current_pos)

    def _on_scrub(self, val: int) -> None:
        ratio = val / 1000.0
        self._current_pos = ratio * self._duration
        self.waveform.set_progress(ratio)
        self._update_time_label()

    def _update_time_label(self) -> None:
        cur_min, cur_sec = divmod(int(self._current_pos), 60)
        dur_min, dur_sec = divmod(int(self._duration), 60)
        self.time_label.setText(f"{cur_min}:{cur_sec:02d} / {dur_min}:{dur_sec:02d}")


class VideoPreviewWidget(QWidget):
    """Video preview with frame thumbnail, scrub bar, and timestamp jumping."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.poster_label = QLabel()
        self.poster_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.poster_label.setStyleSheet("border-radius: 8px; background-color: rgba(0, 0, 0, 0.4);")

        controls = QHBoxLayout()
        self.scrub_slider = QSlider(Qt.Orientation.Horizontal)
        self.scrub_slider.setRange(0, 1000)
        self.scrub_slider.valueChanged.connect(self._on_scrub)

        self.jump_btn = QPushButton("Open at Moment")
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

    def load_video(
        self,
        path: str,
        name: str,
        loc: float = 0.0,
        size: int = 0,
        mtime: float = 0.0,
        reason: str = "",
    ) -> None:
        """Load video metadata and seek to match position."""
        self._path = path
        self._current_pos = loc
        self._duration = max(loc * 1.5, 300.0)

        # Draw a synthetic video frame thumbnail with play emblem
        pixmap = QPixmap(390, 220)
        pixmap.fill(QColor("#1A1D24"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        center_x = pixmap.width() / 2.0
        center_y = pixmap.height() / 2.0

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(ThemeColors.BRAND_START)))
        painter.drawEllipse(QRectF(center_x - 30, center_y - 30, 60, 60))

        painter.setBrush(QBrush(QColor("#FFFFFF")))
        triangle = [
            QPoint(int(center_x - 8), int(center_y - 14)),
            QPoint(int(center_x + 14), int(center_y)),
            QPoint(int(center_x - 8), int(center_y + 14)),
        ]
        painter.drawPolygon(triangle)
        painter.end()

        self.poster_label.setPixmap(pixmap)

        pos_ratio = self._current_pos / self._duration if self._duration > 0 else 0.0
        self.scrub_slider.setValue(int(pos_ratio * 1000))
        self._update_time_label()

        self.meta_card.update_metadata(name, path, size, mtime, reason)

    def _on_scrub(self, val: int) -> None:
        ratio = val / 1000.0
        self._current_pos = ratio * self._duration
        self._update_time_label()

    def _jump_to_timestamp(self) -> None:
        open_media_timestamp(self._path, self._current_pos)

    def _update_time_label(self) -> None:
        cur_min, cur_sec = divmod(int(self._current_pos), 60)
        dur_min, dur_sec = divmod(int(self._duration), 60)
        self.time_label.setText(f"{cur_min}:{cur_sec:02d} / {dur_min}:{dur_sec:02d}")


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
    """Source code snippet preview with gutter and definition line highlight."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.editor = CodeEditor()
        self.meta_card = MetadataCard()

        layout.addWidget(self.editor, stretch=1)
        layout.addWidget(self.meta_card)

    def load_code(
        self,
        path: str,
        name: str,
        line: int = 1,
        size: int = 0,
        mtime: float = 0.0,
        reason: str = "",
    ) -> None:
        """Load code file content and highlight definition line in yellow."""
        target_path = Path(path)
        content = target_path.read_text(encoding="utf-8", errors="replace") if target_path.is_file() else ""
        self.editor.setPlainText(content)

        target_line = max(1, line)
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(
            QTextCursor.MoveOperation.Down,
            QTextCursor.MoveMode.MoveAnchor,
            target_line - 1,
        )

        selection = QTextEditExtraSelectionHelper.create_line_selection(cursor)
        self.editor.setExtraSelections([selection])
        self.editor.setTextCursor(cursor)
        self.editor.centerCursor()

        self.meta_card.update_metadata(name, path, size, mtime, reason)


class QTextEditExtraSelectionHelper:
    """Helper to construct extra selections for QPlainTextEdit."""

    @staticmethod
    def create_line_selection(cursor: QTextCursor):
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor(250, 204, 21, 90))
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selection.cursor = cursor
        return selection


class PreviewPane(QStackedWidget):
    """Stacked multimodal preview pane coordinating all media viewers."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(ThemeLayout.PREVIEW_WIDTH)

        self.empty_view = EmptyPreviewWidget()
        self.image_view = ImagePreviewWidget()
        self.pdf_view = PdfPreviewWidget()
        self.audio_view = AudioPreviewWidget()
        self.video_view = VideoPreviewWidget()
        self.code_view = CodePreviewWidget()

        self.addWidget(self.empty_view)  # 0
        self.addWidget(self.image_view)  # 1
        self.addWidget(self.pdf_view)    # 2
        self.addWidget(self.audio_view)  # 3
        self.addWidget(self.video_view)  # 4
        self.addWidget(self.code_view)   # 5

        self.setCurrentIndex(0)

    def load_result(self, item: ResultRow | SearchHit, query: str = "") -> None:
        """Route item to appropriate multimodal preview renderer."""
        if isinstance(item, SearchHit):
            path = item.path
            name = Path(item.path).name
            kind = item.kind
            loc = item.loc
            focus = int(item.focus) if item.focus > 0 else int(item.loc)
            size = item.size
            mtime = item.modified
            reason = "Matches meaning" if item.z >= 2.0 else "Matches words"
        else:
            path = item.path if item.path else str(Path(item.folder) / item.title)
            name = item.title
            kind = item.kind
            loc = float(item.code_line or 0.0)
            focus = item.code_line or 1
            size = 0
            mtime = 0.0
            reason = item.reason

        suffix = Path(path).suffix.lower()

        if kind in (FileKind.IMAGE, FileKind.SCREENSHOT) or suffix in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"):
            self.image_view.load_image(path, name, size, mtime, reason)
            self.setCurrentIndex(1)
        elif kind == FileKind.PDF or suffix == ".pdf":
            page = int(loc) if loc > 0 else 1
            self.pdf_view.load_pdf(path, name, page, query, size, mtime, reason)
            self.setCurrentIndex(2)
        elif kind == FileKind.AUDIO or suffix in (".mp3", ".wav", ".ogg", ".flac", ".m4a"):
            self.audio_view.load_audio(path, name, loc, size, mtime, reason)
            self.setCurrentIndex(3)
        elif kind == FileKind.VIDEO or suffix in (".mp4", ".mkv", ".webm", ".avi", ".mov"):
            self.video_view.load_video(path, name, loc, size, mtime, reason)
            self.setCurrentIndex(4)
        elif kind == FileKind.CODE or suffix in (
            ".py", ".js", ".ts", ".rs", ".go", ".c", ".cpp", ".h", ".json", ".yaml", ".yml", ".toml", ".md", ".sh"
        ):
            self.code_view.load_code(path, name, focus, size, mtime, reason)
            self.setCurrentIndex(5)
        else:
            # Fallback to code/text preview if text file or empty
            self.code_view.load_code(path, name, 1, size, mtime, reason)
            self.setCurrentIndex(5)

    def clear(self) -> None:
        """Reset preview pane to empty placeholder view."""
        self.setCurrentIndex(0)
