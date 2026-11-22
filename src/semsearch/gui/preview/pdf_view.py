"""PDF page renderer with PyMuPDF, HiDPI scaling, error handling, and term highlights."""

from pathlib import Path

import pymupdf
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap, QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from semsearch.config import AppConfig
from semsearch.core.search_text import extract_terms
from semsearch.core.types import SearchHit
from semsearch.gui.preview.metadata import MetadataCard
from semsearch.gui.theme import get_palette, get_ui_font
from semsearch.i18n import tr

_PAGE_INFO_TEMPLATE = "Page {current} of {total}"
_BASE_PDF_ZOOM = 1.6
_PADDING_PX = 12


class PdfPreviewWidget(QWidget):
    """PDF page renderer with PyMuPDF and yellow search term highlights."""

    def __init__(self, config: AppConfig | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.page_label = QLabel()
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setMinimumSize(200, 160)
        self.page_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        nav_row = QHBoxLayout()
        self.prev_btn = QPushButton(tr("◀ Prev"))
        self.prev_btn.clicked.connect(self.prev_page)
        self.page_info = QLabel(tr(_PAGE_INFO_TEMPLATE, current=1, total=1))
        self.page_info.setFont(get_ui_font(11))
        self.page_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next_btn = QPushButton(tr("Next ▶"))
        self.next_btn.clicked.connect(self.next_page)

        nav_row.addWidget(self.prev_btn)
        nav_row.addWidget(self.page_info, stretch=1)
        nav_row.addWidget(self.next_btn)

        self.meta_card = MetadataCard(config=config)

        layout.addWidget(self.page_label, stretch=1)
        layout.addLayout(nav_row)
        layout.addWidget(self.meta_card)
        self.refresh_theme()

        self._doc: pymupdf.Document | None = None
        self._current_page: int = 0
        self._total_pages: int = 0
        self._query_terms: list[str] = []
        self._rendered_pixmap = QPixmap()
        self._doc_name: str = ""

    def refresh_theme(self) -> None:
        """Apply current theme palette to PDF preview canvas, page indicator, and metadata card."""
        p = get_palette()
        self.page_label.setStyleSheet(f"border-radius: 8px; background-color: {p.bg_subtle};")
        self.page_info.setStyleSheet(f"color: {p.text_secondary};")
        self.meta_card.refresh_theme()

    def retranslate_ui(self) -> None:
        """Refresh translated navigation buttons and page info."""
        self.prev_btn.setText(tr("◀ Prev"))
        self.next_btn.setText(tr("Next ▶"))
        if self._total_pages > 0:
            self.page_info.setText(tr(_PAGE_INFO_TEMPLATE, current=self._current_page + 1, total=self._total_pages))
        else:
            self.page_info.setText(tr(_PAGE_INFO_TEMPLATE, current=1, total=1))
        self.meta_card.retranslate_ui()

    def _close_doc(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None

    def _show_error(self, message: str) -> None:
        self._close_doc()
        self._rendered_pixmap = QPixmap()
        self._total_pages = 0
        self._current_page = 0
        self.page_label.clear()
        self.page_label.setText(message)
        self.page_info.setText(tr(_PAGE_INFO_TEMPLATE, current=0, total=0))
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(False)

    def load_pdf(
        self,
        path: str,
        name: str,
        page_number: int = 1,
        query: str = "",
        *,
        size: int = 0,
        mtime: float = 0.0,
        reason: str = "",
        hit: SearchHit | None = None,
    ) -> None:
        """Load target PDF page and highlight query keywords in yellow."""
        self._doc_name = name
        self.meta_card.update_metadata(name, path, size, mtime, reason, hit=hit)
        self._close_doc()

        if not Path(path).is_file():
            self._show_error(tr("File not found: {name}", name=name))
            return

        terms = extract_terms(query)
        self._query_terms = terms or [t for t in query.lower().split() if len(t) > 1]

        try:
            doc = pymupdf.open(path)
            if doc.needs_pass:
                doc.close()
                self._show_error(f"Encrypted PDF: {name}")
                return
            total_pages = len(doc)
        except (pymupdf.FileDataError, RuntimeError, ValueError, OSError):
            self._show_error(f"Unable to open PDF: {name}")
            return

        if total_pages == 0:
            doc.close()
            self._show_error(f"Unable to open PDF: {name}")
            return

        self._doc = doc
        self._total_pages = total_pages
        self._current_page = max(0, min(page_number - 1, self._total_pages - 1))
        self._render_current_page()

    def _render_current_page(self) -> None:
        if self._doc is None or self._total_pages == 0:
            return

        dpr = max(1.0, self.devicePixelRatioF())
        zoom = _BASE_PDF_ZOOM * dpr
        try:
            page = self._doc[self._current_page]
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        except (pymupdf.FileDataError, RuntimeError, ValueError):
            self._show_error(f"Unable to open PDF: {self._doc_name}")
            return

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
        highlight_pen = QPen(QColor(250, 204, 21, 204), 1.5 * dpr)

        for term in self._query_terms:
            rects = page.search_for(term)
            for rect in rects:
                box = QRectF(rect.x0 * zoom, rect.y0 * zoom, (rect.x1 - rect.x0) * zoom, (rect.y1 - rect.y0) * zoom)
                painter.fillRect(box, highlight_brush)
                painter.setPen(highlight_pen)
                painter.drawRect(box)

        painter.end()
        qpix.setDevicePixelRatio(dpr)

        self._rendered_pixmap = qpix
        self._rescale_page()
        self.page_info.setText(tr(_PAGE_INFO_TEMPLATE, current=self._current_page + 1, total=self._total_pages))
        self.prev_btn.setEnabled(self._current_page > 0)
        self.next_btn.setEnabled(self._current_page < self._total_pages - 1)

    def _rescale_page(self) -> None:
        if self._rendered_pixmap.isNull():
            return
        dpr = max(1.0, self.devicePixelRatioF())
        target = self.page_label.size() - QSize(_PADDING_PX, _PADDING_PX)
        phys_w = max(1, int(target.width() * dpr))
        phys_h = max(1, int(target.height() * dpr))
        scaled = self._rendered_pixmap.scaled(
            QSize(phys_w, phys_h),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)
        self.page_label.setPixmap(scaled)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Rescale current rendered page on resize."""
        super().resizeEvent(event)
        self._rescale_page()

    def prev_page(self) -> None:
        """Navigate to the previous page if available."""
        if self._current_page > 0:
            self._current_page -= 1
            self._render_current_page()

    def next_page(self) -> None:
        """Navigate to the next page if available."""
        if self._current_page < self._total_pages - 1:
            self._current_page += 1
            self._render_current_page()

    def _prev_page(self) -> None:
        self.prev_page()

    def _next_page(self) -> None:
        self.next_page()

    def clear(self) -> None:
        """Close open PDF document and release rendered page pixmap."""
        self._close_doc()
        self._rendered_pixmap = QPixmap()
        self.page_label.clear()
        self._total_pages = 0
        self._current_page = 0
        self._query_terms = []
        self._doc_name = ""
        self.meta_card.clear()
