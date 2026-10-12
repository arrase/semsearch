"""Full desktop standalone search window with card grid, code syntax view, and drag & drop."""

from pathlib import Path
from PySide6.QtCore import QMimeData, QPoint, QSize, Qt, QUrl, Signal
from PySide6.QtGui import (
    QDrag,
    QFont,
    QKeySequence,
    QMouseEvent,
    QPixmap,
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
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from semsearch.config import AppConfig
from semsearch.core.types import FileKind, SearchHit
from semsearch.gui.preview import PreviewPane
from semsearch.gui.theme import (
    ThemeColors,
    ThemeLayout,
    apply_theme,
    get_mono_font,
    get_ui_font,
)
from semsearch.services.actions import (
    execute_hit_action,
    open_code_editor,
)
from semsearch.services.searcher import SearchService


KIND_CHIPS = [
    ("All", None, "Ctrl+1"),
    ("PDF", FileKind.PDF, "Ctrl+2"),
    ("Images", FileKind.IMAGE, "Ctrl+3"),
    ("Audio", FileKind.AUDIO, "Ctrl+4"),
    ("Video", FileKind.VIDEO, "Ctrl+5"),
    ("Code", FileKind.CODE, "Ctrl+6"),
    ("Docs", FileKind.DOC, "Ctrl+7"),
]


class ResultCard(QFrame):
    """Visual media and document card with desktop drag-and-drop export."""

    selected = Signal(SearchHit)

    def __init__(self, hit: SearchHit, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hit = hit
        self._drag_start_pos: QPoint | None = None

        self.setFixedSize(180, 160)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # Thumbnail / Kind Emblem
        self.thumb_label = QLabel()
        self.thumb_label.setFixedHeight(64)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setStyleSheet("background-color: rgba(0, 0, 0, 0.2); border-radius: 6px;")

        self._render_thumbnail()

        # Filename
        name = Path(hit.path).name
        self.title_label = QLabel(name)
        self.title_label.setFont(get_ui_font(12, QFont.Weight.DemiBold))
        self.title_label.setStyleSheet(f"color: {ThemeColors.TEXT_PRIMARY};")
        self.title_label.setWordWrap(False)

        # Snippet / Excerpt
        self.snippet_label = QLabel(hit.excerpt if hit.excerpt else hit.path)
        self.snippet_label.setFont(get_ui_font(10))
        self.snippet_label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED};")
        self.snippet_label.setWordWrap(False)

        layout.addWidget(self.thumb_label)
        layout.addWidget(self.title_label)
        layout.addWidget(self.snippet_label)
        layout.addStretch()

    def _render_thumbnail(self) -> None:
        suffix = Path(self.hit.path).suffix.lower()
        if suffix in (".png", ".jpg", ".jpeg", ".webp", ".bmp") and Path(self.hit.path).is_file():
            pix = QPixmap(self.hit.path)
            if not pix.isNull():
                scaled = pix.scaled(
                    QSize(160, 64),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.thumb_label.setPixmap(scaled)
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
            execute_hit_action(self.hit)
        super().mouseDoubleClickEvent(event)

    def _start_drag(self) -> None:
        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(self.hit.path)])
        drag.setMimeData(mime_data)
        drag.exec_(Qt.DropAction.CopyAction)


class CardGridView(QScrollArea):
    """Responsive scrolling flow grid for visual and document cards."""

    card_selected = Signal(SearchHit)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setStyleSheet("background: transparent; border: none;")

        self.container = QWidget()
        self.grid_layout = QGridLayout(self.container)
        self.grid_layout.setContentsMargins(12, 12, 12, 12)
        self.grid_layout.setSpacing(14)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self.setWidget(self.container)
        self._cards: list[ResultCard] = []

    def set_results(self, hits: list[SearchHit]) -> None:
        """Populate grid with cards matching results."""
        # Clear existing cards
        while self.grid_layout.count() > 0:
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cards.clear()

        columns = 3
        for idx, hit in enumerate(hits):
            card = ResultCard(hit)
            card.selected.connect(self.card_selected.emit)
            row = idx // columns
            col = idx % columns
            self.grid_layout.addWidget(card, row, col)
            self._cards.append(card)


class CodeListView(QListWidget):
    """Structured table/list for code matches with jump to definition line."""

    code_selected = Signal(SearchHit)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFont(get_mono_font(12))
        self.itemClicked.connect(self._on_item_clicked)
        self.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._drag_start_pos: QPoint | None = None

    def set_results(self, hits: list[SearchHit]) -> None:
        """Populate list with code result items."""
        self.clear()
        for hit in hits:
            line_str = f"L{int(hit.focus)}" if hit.focus > 0 else (f"L{int(hit.loc)}" if hit.loc > 0 else "")
            display_text = f"{Path(hit.path).name} : {line_str}  —  {hit.excerpt.strip()}"

            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, hit)
            self.addItem(item)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        hit = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(hit, SearchHit):
            self.code_selected.emit(hit)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        hit = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(hit, SearchHit):
            target_line = int(hit.focus) if hit.focus > 0 else int(hit.loc)
            open_code_editor(hit.path, line=target_line)

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
        self._active_kind: FileKind | None = None
        self._chip_buttons: dict[FileKind | None, QPushButton] = {}

        self._build_ui()
        self._setup_shortcuts()
        apply_theme(self)

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # Header Search Area
        search_row = QHBoxLayout()
        search_row.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search documents, media, images, and source code...")
        self.search_input.setFixedHeight(40)
        self.search_input.setFont(get_ui_font(14))
        self.search_input.textChanged.connect(self._on_search_text_changed)

        self.toggle_preview_btn = QPushButton("Preview")
        self.toggle_preview_btn.setCheckable(True)
        self.toggle_preview_btn.setChecked(True)
        self.toggle_preview_btn.toggled.connect(self._toggle_preview_sidebar)

        search_row.addWidget(self.search_input, stretch=1)
        search_row.addWidget(self.toggle_preview_btn)

        # Kind Chips Filter Bar
        chips_layout = QHBoxLayout()
        chips_layout.setSpacing(8)
        self.chip_group = QButtonGroup(self)
        self.chip_group.setExclusive(True)

        for label, kind, shortcut in KIND_CHIPS:
            btn = QPushButton(f"{label} ({shortcut})")
            btn.setCheckable(True)
            if kind is None:
                btn.setChecked(True)
            btn.clicked.connect(lambda checked, k=kind: self.set_kind_filter(k))
            self.chip_group.addButton(btn)
            chips_layout.addWidget(btn)
            self._chip_buttons[kind] = btn

        chips_layout.addStretch()

        main_layout.addLayout(search_row)
        main_layout.addLayout(chips_layout)

        # Main Splitter
        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        # Content Area Stack (Grid vs Code List)
        self.content_stack = QStackedWidget()
        self.card_grid = CardGridView()
        self.card_grid.card_selected.connect(self._on_hit_selected)
        self.code_list = CodeListView()
        self.code_list.code_selected.connect(self._on_hit_selected)

        self.content_stack.addWidget(self.card_grid)  # 0
        self.content_stack.addWidget(self.code_list)  # 1

        # Right Preview Sidebar
        self.preview_pane = PreviewPane()
        self.preview_pane.setFixedWidth(ThemeLayout.PREVIEW_WIDTH)

        self.splitter.addWidget(self.content_stack)
        self.splitter.addWidget(self.preview_pane)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)

        main_layout.addWidget(self.splitter, stretch=1)

    def _setup_shortcuts(self) -> None:
        # Ctrl+1 through Ctrl+7 shortcuts for kind chips
        for idx, (_, kind, shortcut_key) in enumerate(KIND_CHIPS, start=1):
            sc = QShortcut(QKeySequence(shortcut_key), self)
            sc.activated.connect(lambda k=kind: self.set_kind_filter(k))

        # Ctrl+F focuses search input
        focus_sc = QShortcut(QKeySequence("Ctrl+F"), self)
        focus_sc.activated.connect(self.search_input.setFocus)

    def set_kind_filter(self, kind: FileKind | None) -> None:
        """Switch active kind filter and refresh displayed results."""
        self._active_kind = kind
        btn = self._chip_buttons.get(kind)
        if btn and not btn.isChecked():
            btn.setChecked(True)

        self._refresh_filtered_view()

    def _on_search_text_changed(self, text: str) -> None:
        stripped = text.strip()
        if not stripped:
            self._current_hits = []
            self.card_grid.set_results([])
            self.code_list.set_results([])
            self.preview_pane.clear()
            self._update_chip_badges([])
            return

        result_set = self.search_service.search(stripped, limit=100)
        self._current_hits = result_set.ranked + result_set.weaker
        self._update_chip_badges(self._current_hits)
        self._refresh_filtered_view()

    def _update_chip_badges(self, hits: list[SearchHit]) -> None:
        counts: dict[FileKind | None, int] = {None: len(hits)}
        for h in hits:
            counts[h.kind] = counts.get(h.kind, 0) + 1

        for label, kind, shortcut in KIND_CHIPS:
            count = counts.get(kind, 0)
            btn = self._chip_buttons.get(kind)
            if btn:
                btn.setText(f"{label} ({count})")

    def _refresh_filtered_view(self) -> None:
        if self._active_kind is None:
            filtered = self._current_hits
        else:
            filtered = [h for h in self._current_hits if h.kind == self._active_kind]

        if self._active_kind == FileKind.CODE:
            self.content_stack.setCurrentIndex(1)
            self.code_list.set_results(filtered)
        else:
            self.content_stack.setCurrentIndex(0)
            self.card_grid.set_results(filtered)

        if filtered:
            self._on_hit_selected(filtered[0])
        else:
            self.preview_pane.clear()

    def _on_hit_selected(self, hit: SearchHit) -> None:
        self.preview_pane.load_result(hit, query=self.search_input.text())

    def _toggle_preview_sidebar(self, checked: bool) -> None:
        self.preview_pane.setVisible(checked)

    def perform_search(self, query: str) -> None:
        """Programmatically trigger search in window."""
        self.search_input.setText(query)
