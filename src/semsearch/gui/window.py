"""Full desktop standalone search window with card grid, code syntax view, and drag & drop."""

from pathlib import Path
import pymupdf
from PySide6.QtCore import QEvent, QMimeData, QObject, QPoint, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QDrag,
    QFont,
    QImage,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPixmap,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from semsearch.config import AppConfig
from semsearch.core.types import FileKind, SearchHit
from semsearch.gui.panel import format_hit_place_badge
from semsearch.gui.preview import PreviewPane, _extract_video_frame, format_tilde_path
from semsearch.gui.theme import (
    ThemeColors,
    ThemeLayout,
    apply_theme,
    get_mono_font,
    get_ui_font,
)
from semsearch.gui.widgets import ElidedLabel
from semsearch.gui.worker import SearchWorker
from semsearch.i18n import tr
from semsearch.services.actions import (
    execute_hit_action,
    open_code_at,
)
from semsearch.services.searcher import ResultSet, SearchService


KIND_CHIPS: list[tuple[str, FileKind | None, str]] = [
    ("All", None, "Ctrl+1"),
    ("PDF", FileKind.PDF, "Ctrl+2"),
    ("Images", FileKind.IMAGE, "Ctrl+3"),
    ("Screenshots", FileKind.SCREENSHOT, "Ctrl+4"),
    ("Audio", FileKind.AUDIO, "Ctrl+5"),
    ("Video", FileKind.VIDEO, "Ctrl+6"),
    ("Code", FileKind.CODE, "Ctrl+7"),
    ("Docs", FileKind.DOC, "Ctrl+8"),
]


def _clean_single_line(text: str) -> str:
    """Strip FTS markers and collapse whitespace into a single clean line."""
    cleaned = text.replace("«", "").replace("»", "")
    return " ".join(cleaned.split())


class ResultCard(QFrame):
    """Visual media and document card with selection state and desktop drag-and-drop export."""

    selected = Signal(SearchHit)

    def __init__(self, hit: SearchHit, config: AppConfig | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hit = hit
        self.config = config
        self._drag_start_pos: QPoint | None = None
        self._is_selected = False

        self.setFixedSize(196, 172)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_card_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        self.thumb_label = QLabel()
        self.thumb_label.setFixedHeight(84)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setStyleSheet("background-color: rgba(0, 0, 0, 0.24); border-radius: 6px; border: none;")

        self._render_thumbnail()

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(4)

        self.title_label = ElidedLabel(Path(hit.path).name)
        self.title_label.setFont(get_ui_font(11, QFont.Weight.DemiBold))
        self.title_label.setStyleSheet(f"color: {ThemeColors.TEXT_PRIMARY}; background: transparent; border: none;")
        title_row.addWidget(self.title_label, stretch=1)

        badge_str = format_hit_place_badge(hit)
        if badge_str:
            self.place_badge = QLabel(badge_str)
            self.place_badge.setFont(get_ui_font(9, QFont.Weight.Medium))
            self.place_badge.setStyleSheet(
                f"color: {ThemeColors.TEXT_SECONDARY}; background-color: rgba(255, 255, 255, 0.08); "
                "border-radius: 4px; padding: 1px 4px; border: none;"
            )
            title_row.addWidget(self.place_badge)

        raw_snippet = _clean_single_line(hit.excerpt) if hit.excerpt else format_tilde_path(str(Path(hit.path).parent))
        self.snippet_label = ElidedLabel(raw_snippet, Qt.TextElideMode.ElideRight)
        self.snippet_label.setFont(get_ui_font(10))
        self.snippet_label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; background: transparent; border: none;")

        layout.addWidget(self.thumb_label)
        layout.addLayout(title_row)
        layout.addWidget(self.snippet_label)
        layout.addStretch()

    def set_selected(self, selected: bool) -> None:
        """Update visual selection border and background."""
        if self._is_selected != selected:
            self._is_selected = selected
            self._apply_card_style()

    def _apply_card_style(self) -> None:
        if self._is_selected:
            self.setStyleSheet(
                f"ResultCard {{"
                f"  background-color: rgba(99, 102, 241, 0.16);"
                f"  border: 1.5px solid {ThemeColors.BRAND_START};"
                f"  border-radius: 10px;"
                f"}}"
            )
        else:
            self.setStyleSheet(
                f"ResultCard {{"
                f"  background-color: {ThemeColors.BG_CARD};"
                f"  border: 1px solid {ThemeColors.BORDER};"
                f"  border-radius: 10px;"
                f"}}"
                f"ResultCard:hover {{"
                f"  border-color: {ThemeColors.BRAND_START};"
                f"  background-color: {ThemeColors.BG_CARD_HOVER};"
                f"}}"
            )

    def _render_thumbnail(self) -> None:
        path_obj = Path(self.hit.path)
        suffix = path_obj.suffix.lower()
        target_size = QSize(176, 84)

        if suffix in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif") and path_obj.is_file():
            pix = QPixmap(self.hit.path)
            if not pix.isNull():
                self.thumb_label.setPixmap(
                    pix.scaled(
                        target_size,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                return

        if (self.hit.kind == FileKind.PDF or suffix == ".pdf") and path_obj.is_file():
            try:
                doc = pymupdf.open(self.hit.path)
                if len(doc) > 0:
                    page_idx = max(0, min(int(self.hit.loc) - 1, len(doc) - 1))
                    pix = doc[page_idx].get_pixmap(matrix=pymupdf.Matrix(0.35, 0.35), alpha=False)
                    qimg = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888)
                    qpix = QPixmap.fromImage(qimg.copy())
                    doc.close()
                    if not qpix.isNull():
                        self.thumb_label.setPixmap(
                            qpix.scaled(
                                target_size,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation,
                            )
                        )
                        return
                doc.close()
            except Exception:
                pass

        if (self.hit.kind == FileKind.VIDEO or suffix in (".mp4", ".mkv", ".webm", ".avi", ".mov")) and path_obj.is_file():
            frame_pix = _extract_video_frame(self.hit.path, self.hit.loc)
            if not frame_pix.isNull():
                self.thumb_label.setPixmap(
                    frame_pix.scaled(
                        target_size,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                return

        kind_icons = {
            FileKind.PDF: "📕 PDF",
            FileKind.DOC: "📄 DOC",
            FileKind.IMAGE: "🖼️ IMG",
            FileKind.SCREENSHOT: "📸 CAP",
            FileKind.AUDIO: "🎵 AUD",
            FileKind.VIDEO: "🎬 VID",
            FileKind.CODE: "💻 SRC",
        }
        self.thumb_label.setText(kind_icons.get(self.hit.kind, "📁 FILE"))
        self.thumb_label.setFont(get_ui_font(12, QFont.Weight.Bold))
        self.thumb_label.setStyleSheet(
            f"color: {ThemeColors.TEXT_SECONDARY}; background-color: rgba(0, 0, 0, 0.24); "
            "border-radius: 6px; border: none;"
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()
            self.selected.emit(self.hit)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_start_pos is not None:
            distance = (event.position().toPoint() - self._drag_start_pos).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._start_drag()
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            execute_hit_action(self.hit, self.config)
        super().mouseDoubleClickEvent(event)

    def _start_drag(self) -> None:
        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(self.hit.path)])
        drag.setMimeData(mime_data)
        drag.exec_(Qt.DropAction.CopyAction)


class CardGridView(QScrollArea):
    """Responsive scrolling flow grid for visual and document cards with weaker-match folding."""

    card_selected = Signal(SearchHit)

    def __init__(self, config: AppConfig | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("background: transparent; border: none;")

        self.container = QWidget()
        self.outer_layout = QVBoxLayout(self.container)
        self.outer_layout.setContentsMargins(12, 12, 12, 12)
        self.outer_layout.setSpacing(12)

        self.empty_label = QLabel(
            tr("Type a query above to search across your documents, media, and code")
        )
        self.empty_label.setFont(get_ui_font(13))
        self.empty_label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; padding: 48px 16px;")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.outer_layout.addWidget(self.empty_label)

        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setSpacing(14)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.outer_layout.addWidget(self.grid_widget)

        self.weaker_btn = QPushButton()
        self.weaker_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.weaker_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {ThemeColors.BRAND_START}; "
            f"border: none; text-align: left; font-weight: 600; padding: 4px 2px; }}"
            f"QPushButton:hover {{ color: {ThemeColors.TEXT_PRIMARY}; }}"
        )
        self.weaker_btn.clicked.connect(self._toggle_weaker)
        self.weaker_btn.setVisible(False)
        self.outer_layout.addWidget(self.weaker_btn)

        self.weaker_grid_widget = QWidget()
        self.weaker_grid_layout = QGridLayout(self.weaker_grid_widget)
        self.weaker_grid_layout.setContentsMargins(0, 0, 0, 0)
        self.weaker_grid_layout.setSpacing(14)
        self.weaker_grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.weaker_grid_widget.setVisible(False)
        self.outer_layout.addWidget(self.weaker_grid_widget)

        self.outer_layout.addStretch(1)

        self.setWidget(self.container)
        self._cards: list[ResultCard] = []
        self._weaker_cards: list[ResultCard] = []
        self._weaker_expanded = False
        self._columns = 3
        self._last_query = ""

    def retranslate_ui(self) -> None:
        """Refresh translated grid view labels."""
        if not self._cards and not self._weaker_cards:
            self.empty_label.setText(
                tr('No matches found for "{query}"', query=self._last_query)
                if self._last_query.strip()
                else tr("Type a query above to search across your documents, media, and code")
            )
        if self._weaker_cards:
            self._update_weaker_btn_text(len(self._weaker_cards))

    def _compute_columns(self) -> int:
        avail = self.viewport().width()
        if avail < 220:
            return 3
        return max(1, (avail - 24) // 210)

    def set_results(
        self,
        hits: list[SearchHit],
        weaker: list[SearchHit] | None = None,
        query: str = "",
    ) -> None:
        """Populate grid with ranked cards and optional expandable weaker matches."""
        self._last_query = query
        self._clear_grid(self.grid_layout)
        self._clear_grid(self.weaker_grid_layout)
        self._cards.clear()
        self._weaker_cards.clear()

        weaker_list = weaker or []
        if not hits and not weaker_list:
            self.empty_label.setText(
                tr('No matches found for "{query}"', query=query)
                if query.strip()
                else tr("Type a query above to search across your documents, media, and code")
            )
            self.empty_label.setVisible(True)
            self.weaker_btn.setVisible(False)
            self.weaker_grid_widget.setVisible(False)
            return

        self.empty_label.setVisible(False)
        self._columns = self._compute_columns()

        for idx, hit in enumerate(hits):
            card = ResultCard(hit, config=self.config)
            card.selected.connect(self._on_card_clicked)
            self.grid_layout.addWidget(card, idx // self._columns, idx % self._columns)
            self._cards.append(card)

        if weaker_list:
            self.weaker_btn.setVisible(True)
            self._update_weaker_btn_text(len(weaker_list))
            for idx, hit in enumerate(weaker_list):
                card = ResultCard(hit, config=self.config)
                card.selected.connect(self._on_card_clicked)
                self.weaker_grid_layout.addWidget(card, idx // self._columns, idx % self._columns)
                self._weaker_cards.append(card)
            self.weaker_grid_widget.setVisible(self._weaker_expanded)
        else:
            self.weaker_btn.setVisible(False)
            self.weaker_grid_widget.setVisible(False)

    def select_hit(self, target: SearchHit) -> None:
        """Highlight the card matching target."""
        for card in self._cards + self._weaker_cards:
            card.set_selected(card.hit is target)

    def _on_card_clicked(self, hit: SearchHit) -> None:
        self.select_hit(hit)
        self.card_selected.emit(hit)

    def move_selection(self, step: int) -> None:
        """Select the visible card `step` positions away from the current one."""
        cards = self._cards + (self._weaker_cards if self._weaker_expanded else [])
        if not cards:
            return
        current = next((i for i, card in enumerate(cards) if card._is_selected), -1)
        target = cards[max(0, min(current + step, len(cards) - 1))]
        self._on_card_clicked(target.hit)
        self.ensureWidgetVisible(target)

    def _toggle_weaker(self) -> None:
        self._weaker_expanded = not self._weaker_expanded
        self._update_weaker_btn_text(len(self._weaker_cards))
        self.weaker_grid_widget.setVisible(self._weaker_expanded)

    def _update_weaker_btn_text(self, count: int) -> None:
        arrow = "▾" if self._weaker_expanded else "▸"
        self.weaker_btn.setText(tr("{arrow} {count} weaker matches", arrow=arrow, count=count))

    def _clear_grid(self, layout: QGridLayout) -> None:
        while layout.count() > 0:
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _reflow_grids(self) -> None:
        new_cols = self._compute_columns()
        if new_cols == self._columns or new_cols < 1:
            return
        self._columns = new_cols
        for idx, card in enumerate(self._cards):
            self.grid_layout.addWidget(card, idx // self._columns, idx % self._columns)
        for idx, card in enumerate(self._weaker_cards):
            self.weaker_grid_layout.addWidget(card, idx // self._columns, idx % self._columns)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._reflow_grids()


class CodeListView(QListWidget):
    """Structured table/list for code matches with jump to definition line."""

    code_selected = Signal(SearchHit)

    def __init__(self, config: AppConfig | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.setObjectName("codeResultList")
        self.setFont(get_mono_font(12))
        self.itemClicked.connect(self._on_item_clicked)
        self.currentItemChanged.connect(self._on_current_item_changed)
        self.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._drag_start_pos: QPoint | None = None

    def set_results(self, hits: list[SearchHit]) -> None:
        """Populate list with single-line code result items."""
        self.clear()
        for hit in hits:
            line_num = int(hit.focus) if hit.focus > 0 else int(hit.loc)
            line_str = f"L{line_num}" if line_num > 0 else "L1"
            rel_path = hit.info.get("repo_rel_path") if hit.info else None
            label_path = str(rel_path) if rel_path else Path(hit.path).name
            def_name = hit.info.get("def_name") if hit.info else None
            symbol_prefix = f"[{def_name}] " if def_name else ""
            snippet = _clean_single_line(hit.excerpt)
            display_text = f"{label_path}:{line_str}  —  {symbol_prefix}{snippet}"

            item = QListWidgetItem(display_text)
            item.setSizeHint(QSize(300, 36))
            item.setData(Qt.ItemDataRole.UserRole, hit)
            self.addItem(item)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        hit = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(hit, SearchHit):
            self.code_selected.emit(hit)

    def _on_current_item_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        hit = current.data(Qt.ItemDataRole.UserRole)
        if isinstance(hit, SearchHit):
            self.code_selected.emit(hit)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        hit = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(hit, SearchHit):
            target_line = int(hit.focus) if hit.focus > 0 else int(hit.loc)
            pref = self.config.code_editor if self.config else "auto"
            open_code_at(hit.path, line=target_line, editor_preference=pref)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            current = self.currentItem()
            if current is not None:
                self._on_item_double_clicked(current)
                return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_start_pos is not None:
            distance = (event.position().toPoint() - self._drag_start_pos).manhattanLength()
            if distance >= QApplication.startDragDistance():
                current = self.currentItem()
                if current is not None:
                    hit = current.data(Qt.ItemDataRole.UserRole)
                    if isinstance(hit, SearchHit):
                        drag = QDrag(self)
                        mime_data = QMimeData()
                        mime_data.setUrls([QUrl.fromLocalFile(hit.path)])
                        drag.setMimeData(mime_data)
                        drag.exec_(Qt.DropAction.CopyAction)
        super().mouseMoveEvent(event)


class SearchWindow(QMainWindow):
    """Standalone 1040x700 search window with kind filters and collapsible preview."""

    settings_requested = Signal()

    def __init__(
        self,
        search_service: SearchService,
        app_config: AppConfig | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.search_service = search_service
        self.config = app_config or AppConfig()

        self.setWindowTitle("SemSearch")
        self.resize(ThemeLayout.WINDOW_WIDTH, ThemeLayout.WINDOW_HEIGHT)
        self.setMinimumSize(ThemeLayout.WINDOW_MIN_WIDTH, ThemeLayout.WINDOW_MIN_HEIGHT)

        self._current_hits: list[SearchHit] = []
        self._ranked_hits: list[SearchHit] = []
        self._weaker_hits: list[SearchHit] = []
        self._selected_hit: SearchHit | None = None
        self._active_kind: FileKind | None = None
        self._chip_buttons: dict[FileKind | None, QPushButton] = {}
        self._worker: SearchWorker | None = None

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(200)
        self._debounce_timer.timeout.connect(self._trigger_async_search)

        self._build_ui()
        self._setup_shortcuts()
        apply_theme(self)

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 16, 16, 12)
        main_layout.setSpacing(12)

        search_row = QHBoxLayout()
        search_row.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            tr("Search documents, media, images, and source code... (Ctrl+F)")
        )
        self.search_input.setFixedHeight(40)
        self.search_input.setFont(get_ui_font(14))
        self.search_input.textChanged.connect(self._on_search_text_changed)
        self.search_input.installEventFilter(self)

        self.toggle_preview_btn = QPushButton(tr("Preview"))
        self.toggle_preview_btn.setCheckable(True)
        self.toggle_preview_btn.setChecked(True)
        self.toggle_preview_btn.setFixedHeight(40)
        self.toggle_preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_preview_btn.setStyleSheet(
            f"QPushButton:checked {{ background-color: rgba(99, 102, 241, 0.16); "
            f"border-color: {ThemeColors.BRAND_START}; color: {ThemeColors.TEXT_PRIMARY}; }}"
        )
        self.toggle_preview_btn.toggled.connect(self._toggle_preview_sidebar)

        self.settings_btn = QPushButton(tr("⚙ Settings"))
        self.settings_btn.setFixedHeight(40)
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.clicked.connect(self.settings_requested.emit)

        search_row.addWidget(self.search_input, stretch=1)
        search_row.addWidget(self.toggle_preview_btn)
        search_row.addWidget(self.settings_btn)

        chips_layout = QHBoxLayout()
        chips_layout.setSpacing(8)
        self.chip_group = QButtonGroup(self)
        self.chip_group.setExclusive(True)

        # Qt silently drops border-radius larger than half the widget height, so height and radius are pinned together.
        chip_style = (
            f"QPushButton {{ background-color: {ThemeColors.BG_CARD}; color: {ThemeColors.TEXT_SECONDARY}; "
            f"border: 1px solid {ThemeColors.BORDER}; border-radius: 13px; padding: 0px 12px; font-size: 12px; }}"
            f"QPushButton:hover {{ border-color: {ThemeColors.BRAND_START}; color: {ThemeColors.TEXT_PRIMARY}; }}"
            f"QPushButton:checked {{ background-color: {ThemeColors.BRAND_START}; color: #FFFFFF; "
            f"border-color: {ThemeColors.BRAND_START}; }}"
        )

        for label, kind, shortcut in KIND_CHIPS:
            translated_label = tr(label)
            btn = QPushButton(translated_label)
            btn.setToolTip(f"{translated_label} ({shortcut})")
            btn.setCheckable(True)
            btn.setFixedHeight(28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(chip_style)
            if kind is None:
                btn.setChecked(True)
            btn.clicked.connect(lambda checked, k=kind: self.set_kind_filter(k))
            self.chip_group.addButton(btn)
            chips_layout.addWidget(btn)
            self._chip_buttons[kind] = btn

        chips_layout.addStretch()

        main_layout.addLayout(search_row)
        main_layout.addLayout(chips_layout)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)

        self.content_stack = QStackedWidget()
        self.card_grid = CardGridView(config=self.config)
        self.card_grid.card_selected.connect(self._on_hit_selected)
        self.code_list = CodeListView(config=self.config)
        self.code_list.code_selected.connect(self._on_hit_selected)

        self.content_stack.addWidget(self.card_grid)  # 0
        self.content_stack.addWidget(self.code_list)  # 1

        self.preview_pane = PreviewPane()
        self.preview_pane.setMinimumWidth(360)

        self.splitter.addWidget(self.content_stack)
        self.splitter.addWidget(self.preview_pane)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([600, 408])

        main_layout.addWidget(self.splitter, stretch=1)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(4, 0, 4, 0)
        self.status_label = QLabel(tr("Ready"))
        self.status_label.setFont(get_ui_font(11))
        self.status_label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED};")
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        main_layout.addLayout(status_row)

    def retranslate_ui(self) -> None:
        """Refresh translated labels across window components."""
        self.search_input.setPlaceholderText(
            tr("Search documents, media, images, and source code... (Ctrl+F)")
        )
        self.toggle_preview_btn.setText(tr("Preview"))
        self.settings_btn.setText(tr("⚙ Settings"))
        self._update_chip_badges(self._current_hits)
        self.card_grid.retranslate_ui()
        self.preview_pane.retranslate_ui()
        self._refresh_filtered_view()

    def _setup_shortcuts(self) -> None:
        for _, kind, shortcut_key in KIND_CHIPS:
            sc = QShortcut(QKeySequence(shortcut_key), self)
            sc.activated.connect(lambda k=kind: self.set_kind_filter(k))

        focus_sc = QShortcut(QKeySequence("Ctrl+F"), self)
        focus_sc.activated.connect(self.search_input.setFocus)

    def set_kind_filter(self, kind: FileKind | None) -> None:
        """Switch active kind filter and refresh displayed results."""
        old_kind = self._active_kind
        self._active_kind = kind
        btn = self._chip_buttons.get(kind)
        if btn and not btn.isChecked():
            btn.setChecked(True)

        if kind != FileKind.CODE and self.search_input.text().strip().lower().startswith("code:"):
            raw_text = self.search_input.text().strip()
            self.search_input.blockSignals(True)
            self.search_input.setText(raw_text[5:].strip())
            self.search_input.blockSignals(False)

        self._refresh_filtered_view()
        if self.search_input.text().strip():
            if (kind == FileKind.CODE and old_kind != FileKind.CODE) or (old_kind == FileKind.CODE and kind != FileKind.CODE):
                self._trigger_async_search()

    def _stop_workers(self) -> None:
        self._debounce_timer.stop()
        if self._worker is not None:
            self._worker.stop()
            self._worker.wait(500)

    def _clear_results(self) -> None:
        self._stop_workers()
        self._current_hits = []
        self._ranked_hits = []
        self._weaker_hits = []
        self._selected_hit = None
        self.card_grid.set_results([], weaker=[], query="")
        self.code_list.set_results([])
        self.preview_pane.clear()
        self._update_chip_badges([])
        self.status_label.setText(tr("Ready"))

    def _apply_search_results(self, result_set: ResultSet) -> None:
        self._ranked_hits = list(result_set.ranked)
        self._weaker_hits = list(result_set.weaker)
        self._current_hits = self._ranked_hits + self._weaker_hits
        self._update_chip_badges(self._current_hits)
        self._refresh_filtered_view()

    def _on_search_text_changed(self, text: str) -> None:
        stripped = text.strip()
        if not stripped:
            self._clear_results()
            return
        if stripped.lower().startswith("code:") and self._active_kind != FileKind.CODE:
            self.set_kind_filter(FileKind.CODE)
        self.status_label.setText(tr("Searching…"))
        self._debounce_timer.start(200)

    def _trigger_async_search(self) -> None:
        stripped = self.search_input.text().strip()
        if not stripped:
            return
        self._stop_workers()
        is_code = (self._active_kind == FileKind.CODE) or stripped.lower().startswith("code:")
        self._worker = SearchWorker(self.search_service, stripped, limit=100, is_code=is_code)
        self._worker.results_ready.connect(self._on_search_results_ready)
        self._worker.start()

    def _on_search_results_ready(self, query: str, result_set: ResultSet) -> None:
        if query != self.search_input.text().strip():
            return
        self._apply_search_results(result_set)

    def _update_chip_badges(self, hits: list[SearchHit]) -> None:
        counts: dict[FileKind | None, int] = {None: len(hits)}
        for h in hits:
            counts[h.kind] = counts.get(h.kind, 0) + 1

        has_query = bool(self.search_input.text().strip()) or bool(hits)
        for label, kind, shortcut in KIND_CHIPS:
            count = counts.get(kind, 0)
            btn = self._chip_buttons.get(kind)
            if btn:
                translated_label = tr(label)
                btn.setText(f"{translated_label} ({count})" if has_query else translated_label)
                btn.setToolTip(f"{translated_label} ({shortcut})")

    def _refresh_filtered_view(self) -> None:
        query_str = self.search_input.text().strip()
        if self._active_kind is None:
            filtered_ranked = self._ranked_hits
            filtered_weaker = self._weaker_hits
        else:
            filtered_ranked = [h for h in self._ranked_hits if h.kind == self._active_kind]
            filtered_weaker = [h for h in self._weaker_hits if h.kind == self._active_kind]

        all_filtered = filtered_ranked + filtered_weaker
        if self._active_kind == FileKind.CODE:
            self.content_stack.setCurrentIndex(1)
            self.code_list.set_results(all_filtered)
            if all_filtered:
                self.code_list.setCurrentRow(0)
        else:
            self.content_stack.setCurrentIndex(0)
            self.card_grid.set_results(filtered_ranked, weaker=filtered_weaker, query=query_str)

        if all_filtered:
            first_hit = all_filtered[0]
            if self._active_kind != FileKind.CODE:
                self.card_grid.select_hit(first_hit)
            self._on_hit_selected(first_hit)
            if filtered_weaker:
                self.status_label.setText(
                    tr("{ranked} matches (+{weaker} weaker)", ranked=len(filtered_ranked), weaker=len(filtered_weaker))
                )
            else:
                self.status_label.setText(tr("{count} matches", count=len(filtered_ranked)))
        else:
            self._selected_hit = None
            self.preview_pane.clear()
            self.status_label.setText(tr("No matches") if query_str else tr("Ready"))

    def _on_hit_selected(self, hit: SearchHit) -> None:
        self._selected_hit = hit
        self.preview_pane.load_result(hit, query=self.search_input.text())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Drive result navigation from the search box: arrows select, Enter opens, Esc clears."""
        if watched is self.search_input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self._move_selection(1 if key == Qt.Key.Key_Down else -1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._selected_hit is not None:
                execute_hit_action(self._selected_hit, self.config)
                return True
            if key == Qt.Key.Key_Escape and self.search_input.text():
                self.search_input.clear()
                return True
        return super().eventFilter(watched, event)

    def _move_selection(self, step: int) -> None:
        if self._active_kind == FileKind.CODE:
            row = self.code_list.currentRow() + step
            self.code_list.setCurrentRow(max(0, min(row, self.code_list.count() - 1)))
        else:
            self.card_grid.move_selection(step)

    def _toggle_preview_sidebar(self, checked: bool) -> None:
        self.preview_pane.setVisible(checked)

    def perform_search(self, query: str) -> None:
        """Programmatically trigger search in window."""
        self._stop_workers()
        stripped = query.strip()
        if not stripped:
            self.search_input.setText("")
            self._clear_results()
            return
        if stripped.lower().startswith("code:") and self._active_kind != FileKind.CODE:
            self.set_kind_filter(FileKind.CODE)
        self.search_input.blockSignals(True)
        self.search_input.setText(query)
        self.search_input.blockSignals(False)
        is_code = (self._active_kind == FileKind.CODE) or stripped.lower().startswith("code:")
        result_set = self.search_service.search(stripped, limit=100, is_code=is_code)
        self._apply_search_results(result_set)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._stop_workers()
        super().closeEvent(event)
