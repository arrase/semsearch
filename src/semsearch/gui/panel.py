"""Quick search floating panel with instant lexical FTS5, debounced semantic ranking, and preview."""

from pathlib import Path
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from semsearch.config import AppConfig
from semsearch.core.types import FileKind, SearchHit
from semsearch.gui.preview import PreviewPane
from semsearch.gui.theme import ThemeColors, ThemeLayout, apply_theme, get_ui_font
from semsearch.services.actions import (
    copy_file_to_clipboard,
    execute_hit_action,
    reveal_in_file_manager,
)
from semsearch.services.searcher import ResultSet, SearchService


KIND_GROUP_NAMES = {
    FileKind.DOC: "Documents",
    FileKind.PDF: "PDF Documents",
    FileKind.SCREENSHOT: "Screenshots",
    FileKind.IMAGE: "Pictures",
    FileKind.AUDIO: "Audio",
    FileKind.VIDEO: "Media",
    FileKind.CODE: "Code",
}


class ResultItemWidget(QWidget):
    """Custom widget rendered inside search result list rows."""

    def __init__(self, hit: SearchHit, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        self.icon_label = QLabel(self._icon_for_kind(hit.kind))
        self.icon_label.setFixedWidth(20)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        file_name = Path(hit.path).name
        self.title_label = QLabel(file_name)
        self.title_label.setFont(get_ui_font(12, QFont.Weight.DemiBold))
        self.title_label.setStyleSheet(f"color: {ThemeColors.TEXT_PRIMARY};")

        path_text = str(Path(hit.path).parent)
        home = str(Path.home())
        if path_text.startswith(home):
            path_text = "~" + path_text[len(home):]
        self.path_label = QLabel(path_text)
        self.path_label.setFont(get_ui_font(10))
        self.path_label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED};")

        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.path_label)

        layout.addWidget(self.icon_label)
        layout.addLayout(text_layout, stretch=1)

    def _icon_for_kind(self, kind: FileKind) -> str:
        icons = {
            FileKind.DOC: "📄",
            FileKind.PDF: "📕",
            FileKind.IMAGE: "🖼️",
            FileKind.SCREENSHOT: "📸",
            FileKind.AUDIO: "🎵",
            FileKind.VIDEO: "🎬",
            FileKind.CODE: "💻",
        }
        return icons.get(kind, "📁")


class SearchPanel(QWidget):
    """Floating borderless quick search panel matching DigUp desktop design."""

    open_window_requested = Signal()

    def __init__(
        self,
        search_service: SearchService,
        app_config: AppConfig | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.search_service = search_service
        self.config = app_config or AppConfig()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.is_expanded = False
        self.weaker_expanded = False
        self._current_result_set: ResultSet = ResultSet()
        self._last_query = ""

        # Semantic debounce timer (120ms)
        self._semantic_timer = QTimer(self)
        self._semantic_timer.setSingleShot(True)
        self._semantic_timer.setInterval(120)
        self._semantic_timer.timeout.connect(self._on_semantic_debounce_fired)

        self._build_ui()
        self._set_geometry_collapsed()
        apply_theme(self)

    def _build_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # Rounded container frame
        self.container = QFrame(self)
        self.container.setObjectName("panelContainer")
        self.container.setStyleSheet(
            f"#panelContainer {{"
            f"  background-color: {ThemeColors.BG_PANEL};"
            f"  border: 1px solid rgba(255, 255, 255, 0.12);"
            f"  border-radius: {ThemeLayout.PANEL_RADIUS}px;"
            f"}}"
        )
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(12, 10, 12, 10)
        container_layout.setSpacing(8)

        # Header Search Input
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search files, documents, code, media...")
        self.search_input.setFixedHeight(38)
        self.search_input.setFont(get_ui_font(14))
        self.search_input.textChanged.connect(self._on_query_text_changed)
        self.search_input.installEventFilter(self)

        container_layout.addWidget(self.search_input)

        # Results and preview split view container
        self.content_widget = QWidget()
        content_layout = QHBoxLayout(self.content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)

        # Left: Result list (330px)
        self.result_list = QListWidget()
        self.result_list.setFixedWidth(ThemeLayout.LIST_WIDTH)
        self.result_list.currentItemChanged.connect(self._on_selection_changed)
        self.result_list.itemClicked.connect(self._on_item_clicked)
        self.result_list.installEventFilter(self)

        # Right: Preview pane (418px)
        self.preview_pane = PreviewPane()
        self.preview_pane.setFixedWidth(ThemeLayout.PREVIEW_WIDTH)

        content_layout.addWidget(self.result_list)
        content_layout.addWidget(self.preview_pane)

        self.content_widget.setVisible(False)
        container_layout.addWidget(self.content_widget, stretch=1)

        outer_layout.addWidget(self.container)

    def _set_geometry_collapsed(self) -> None:
        self.is_expanded = False
        self.content_widget.setVisible(False)
        self.setFixedSize(ThemeLayout.PANEL_WIDTH, ThemeLayout.PANEL_HEIGHT_COLLAPSED)
        self._reposition_to_top_third()

    def _set_geometry_expanded(self) -> None:
        self.is_expanded = True
        self.content_widget.setVisible(True)
        self.setFixedSize(ThemeLayout.PANEL_WIDTH, ThemeLayout.PANEL_HEIGHT_EXPANDED)
        self._reposition_to_top_third()

    def _reposition_to_top_third(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            x = geo.x() + (geo.width() - ThemeLayout.PANEL_WIDTH) // 2
            y = geo.y() + int(geo.height() * 0.16)
            self.move(x, y)

    def _on_query_text_changed(self, text: str) -> None:
        stripped = text.strip()
        self._last_query = stripped
        if not stripped:
            self._semantic_timer.stop()
            self.result_list.clear()
            self.preview_pane.clear()
            self._set_geometry_collapsed()
            return

        # 1. Instant lexical search on keystroke
        self._execute_instant_lexical(stripped)

        # 2. Debounced semantic vector search after 120ms
        self._semantic_timer.start(120)

    def _execute_instant_lexical(self, query: str) -> None:
        """Run fast keyword match against SQLite index."""
        # Use SearchService with keyword weighting
        target_db = self.search_service.code_db if query.startswith("code:") and self.search_service.code_db else self.search_service.db
        clean_q = query[5:].strip() if query.startswith("code:") else query

        fts_rows = target_db.fts_search(clean_q, limit=50)
        hits: list[SearchHit] = []
        for r in fts_rows:
            file_rec = target_db.find_by_id(r["file_id"])
            if file_rec:
                hits.append(
                    SearchHit(
                        path=file_rec.path,
                        kind=file_rec.kind,
                        score=float(10.0 - min(r["rank"], 9.0)),
                        excerpt=r["snippet"] or r["excerpt"],
                        keyword_rank=r["rank"],
                        segment_id=r["segment_id"],
                        loc=r["loc"] or 0.0,
                    )
                )

        if hits:
            top_score = hits[0].score
            threshold = 0.5 * top_score
            ranked = [h for h in hits if h.score >= threshold]
            weaker = [h for h in hits if h.score < threshold]
            self._current_result_set = ResultSet(ranked=ranked, weaker=weaker)
            self._populate_list(self._current_result_set)
            self._set_geometry_expanded()
        elif not self.is_expanded:
            self._set_geometry_collapsed()

    def _on_semantic_debounce_fired(self) -> None:
        """Execute full hybrid semantic vector search and update results in place."""
        if not self._last_query:
            return
        result_set = self.search_service.search(self._last_query, limit=50)
        self._current_result_set = result_set
        self._populate_list(result_set)
        if len(result_set.ranked) > 0 or len(result_set.weaker) > 0:
            self._set_geometry_expanded()
        else:
            self.preview_pane.clear()
            self._set_geometry_collapsed()

    def _populate_list(self, result_set: ResultSet) -> None:
        self.result_list.clear()

        # Group ranked hits by FileKind
        grouped: dict[FileKind, list[SearchHit]] = {}
        for hit in result_set.ranked:
            grouped.setdefault(hit.kind, []).append(hit)

        for kind, hits in grouped.items():
            # Add Section Header
            header_item = QListWidgetItem(KIND_GROUP_NAMES.get(kind, "Other"))
            header_item.setFont(get_ui_font(10, QFont.Weight.Bold))
            header_item.setForeground(QColor(ThemeColors.TEXT_MUTED))
            header_item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.result_list.addItem(header_item)

            for hit in hits:
                item = QListWidgetItem()
                item.setSizeHint(QSize(ThemeLayout.LIST_WIDTH - 24, 46))
                item.setData(Qt.ItemDataRole.UserRole, hit)
                widget = ResultItemWidget(hit)
                self.result_list.addItem(item)
                self.result_list.setItemWidget(item, widget)

        # Folded Weaker Matches Section
        if result_set.weaker:
            toggle_text = f"▾ {len(result_set.weaker)} weaker matches" if self.weaker_expanded else f"▸ {len(result_set.weaker)} weaker matches"
            toggle_item = QListWidgetItem(toggle_text)
            toggle_item.setFont(get_ui_font(11, QFont.Weight.Medium))
            toggle_item.setForeground(QColor(ThemeColors.BRAND_START))
            toggle_item.setData(Qt.ItemDataRole.UserRole + 1, "toggle")
            self.result_list.addItem(toggle_item)

            if self.weaker_expanded:
                for hit in result_set.weaker:
                    item = QListWidgetItem()
                    item.setSizeHint(QSize(ThemeLayout.LIST_WIDTH - 24, 46))
                    item.setData(Qt.ItemDataRole.UserRole, hit)
                    widget = ResultItemWidget(hit)
                    self.result_list.addItem(item)
                    self.result_list.setItemWidget(item, widget)

        # Auto select first selectable item
        self._select_first_result()

    def _select_first_result(self) -> None:
        for idx in range(self.result_list.count()):
            item = self.result_list.item(idx)
            if item.data(Qt.ItemDataRole.UserRole) is not None:
                self.result_list.setCurrentItem(item)
                break

    def _on_selection_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        hit = current.data(Qt.ItemDataRole.UserRole)
        if isinstance(hit, SearchHit):
            self.preview_pane.load_result(hit, query=self.search_input.text())

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        if item.data(Qt.ItemDataRole.UserRole + 1) == "toggle":
            self.weaker_expanded = not self.weaker_expanded
            self._populate_list(self._current_result_set)

    def eventFilter(self, watched, event) -> bool:
        """Handle keyboard navigation across search input and results list."""
        if event.type() == event.Type.KeyPress:
            key_event: QKeyEvent = event
            key = key_event.key()
            modifiers = key_event.modifiers()

            if key == Qt.Key.Key_Down:
                self._move_selection(1)
                return True
            elif key == Qt.Key.Key_Up:
                self._move_selection(-1)
                return True
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._activate_current_item(modifiers)
                return True
            elif key == Qt.Key.Key_Escape:
                if self.search_input.text():
                    self.search_input.clear()
                    self._set_geometry_collapsed()
                else:
                    self.hide_panel()
                return True
            elif key == Qt.Key.Key_C and (modifiers & Qt.KeyboardModifier.ControlModifier):
                self._copy_selected_path()
                return True
            elif key == Qt.Key.Key_O and (modifiers & Qt.KeyboardModifier.ControlModifier):
                self.hide_panel()
                self.open_window_requested.emit()
                return True

        return super().eventFilter(watched, event)

    def _move_selection(self, step: int) -> None:
        cur_row = self.result_list.currentRow()
        count = self.result_list.count()
        if count == 0:
            return

        next_row = cur_row + step
        while 0 <= next_row < count:
            item = self.result_list.item(next_row)
            if item.data(Qt.ItemDataRole.UserRole) is not None or item.data(Qt.ItemDataRole.UserRole + 1) == "toggle":
                self.result_list.setCurrentRow(next_row)
                break
            next_row += step

    def _activate_current_item(self, modifiers: Qt.KeyboardModifiers) -> None:
        current = self.result_list.currentItem()
        if current is None:
            return

        if current.data(Qt.ItemDataRole.UserRole + 1) == "toggle":
            self.weaker_expanded = not self.weaker_expanded
            self._populate_list(self._current_result_set)
            return

        hit = current.data(Qt.ItemDataRole.UserRole)
        if isinstance(hit, SearchHit):
            if modifiers & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier):
                reveal_in_file_manager(hit.path)
            else:
                execute_hit_action(hit, self.config)
            self.hide_panel()

    def _copy_selected_path(self) -> None:
        current = self.result_list.currentItem()
        if current is not None:
            hit = current.data(Qt.ItemDataRole.UserRole)
            if isinstance(hit, SearchHit):
                copy_file_to_clipboard(hit.path)

    def execute_search(self, query: str) -> None:
        """Trigger search programmatically."""
        self.search_input.setText(query)

    def show_panel(self) -> None:
        """Present search panel with focus."""
        self.search_input.clear()
        self.result_list.clear()
        self.preview_pane.clear()
        self._set_geometry_collapsed()
        self.show()
        self.raise_()
        self.activateWindow()
        self.search_input.setFocus()

    def hide_panel(self) -> None:
        """Dismiss search panel."""
        self.hide()
