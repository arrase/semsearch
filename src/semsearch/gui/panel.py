"""Quick search floating panel with instant lexical FTS5, debounced semantic ranking, and preview."""

import contextlib
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QMimeData, QObject, QSignalBlocker, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QColor, QCursor, QDrag, QFont, QKeyEvent, QScreen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

from semsearch.config import AppConfig
from semsearch.core.ollama import OllamaError
from semsearch.core.types import FileKind, SearchHit
from semsearch.gui.preview import PreviewPane, format_tilde_path
from semsearch.gui.theme import ThemeLayout, apply_theme, get_palette, get_ui_font
from semsearch.gui.widgets import ElidedLabel
from semsearch.gui.worker import SearchWorker
from semsearch.i18n import tr
from semsearch.services.actions import (
    copy_file_to_clipboard,
    copy_to_clipboard,
    execute_hit_action,
    open_terminal_at,
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

_FOOTER_HINT_KEY = "↵ Open    Shift+↵ Show in Files    Ctrl+C Copy    Tab Actions    Esc Dismiss"
_PDF_PREVIEW_INDEX = 2
_PAGE_NAV_STEP = 5
_SCREEN_VERTICAL_MARGIN = 40
_WORKER_SHUTDOWN_WAIT_MS = 1500
_MIN_EXPANDED_HEIGHT = 360
_MAX_WIDGET_SIZE = 16777215


def format_hit_place_badge(hit: SearchHit) -> str:
    """Format a compact location badge (page, timestamp, or line) for a search hit."""
    if hit.kind == FileKind.PDF and hit.loc > 0:
        return tr("p. {page}", page=int(hit.loc))
    if hit.kind in (FileKind.AUDIO, FileKind.VIDEO) and hit.loc > 0:
        mins, secs = divmod(int(hit.loc), 60)
        return f"{mins}:{secs:02d}"
    if hit.kind == FileKind.CODE:
        line = int(hit.focus) if hit.focus > 0 else int(hit.loc)
        if line > 0:
            return f"L{line}"
    return ""


class DraggableResultList(QListWidget):
    """Search result list supporting drag-and-drop via local file URLs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)

    @staticmethod
    def build_mime_data(item: QListWidgetItem) -> QMimeData | None:
        """Build QMimeData with a local file URL when item holds a SearchHit."""
        hit = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(hit, SearchHit):
            return None
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(hit.path)])
        mime.setText(hit.path)
        return mime

    def startDrag(self, _supported_actions: Qt.DropAction) -> None:
        """Initiate a QDrag carrying the selected hit's local file URL."""
        if self.currentRow() < 0:
            return
        mime = self.build_mime_data(self.currentItem())
        if mime is None:
            return
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class ResultItemWidget(QWidget):
    """Custom widget rendered inside search result list rows."""

    def __init__(self, hit: SearchHit, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        self.icon_label = QLabel(self._icon_for_kind(hit.kind))
        self.icon_label.setFont(get_ui_font(14))
        self.icon_label.setFixedWidth(22)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("background: transparent; border: none;")

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        palette = get_palette()
        self.title_label = ElidedLabel(Path(hit.path).name)
        self.title_label.setFont(get_ui_font(12, QFont.Weight.DemiBold))
        self.title_label.setStyleSheet(f"color: {palette.text_primary}; background: transparent; border: none;")
        top_row.addWidget(self.title_label, stretch=1)

        place_text = format_hit_place_badge(hit)
        if place_text:
            self.place_badge = QLabel(place_text)
            self.place_badge.setFont(get_ui_font(10, QFont.Weight.Medium))
            self.place_badge.setStyleSheet(
                f"color: {palette.text_secondary}; background-color: {palette.bg_subtle}; "
                "border-radius: 4px; padding: 1px 5px;"
            )
            top_row.addWidget(self.place_badge)

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(6)

        self.path_label = ElidedLabel(format_tilde_path(str(Path(hit.path).parent)))
        self.path_label.setFont(get_ui_font(10))
        self.path_label.setStyleSheet(f"color: {palette.text_muted}; background: transparent; border: none;")

        self.reason_label = QLabel(tr(hit.reason))
        self.reason_label.setFont(get_ui_font(9))
        self.reason_label.setStyleSheet(f"color: {palette.text_muted}; background: transparent; border: none;")

        bottom_row.addWidget(self.path_label, stretch=1)
        bottom_row.addWidget(self.reason_label)

        text_layout.addLayout(top_row)
        text_layout.addLayout(bottom_row)

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

    def __init__(
        self,
        search_service: SearchService,
        app_config: AppConfig | None = None,
        parent: QWidget | None = None,
        *,
        should_unload_on_hide: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self.search_service = search_service
        self.config = app_config or AppConfig()
        self._should_unload_on_hide = should_unload_on_hide

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.is_expanded = False
        self.weaker_expanded = False
        self._menu_open = False
        self._current_result_set: ResultSet = ResultSet()
        self._last_query = ""
        self._worker: SearchWorker | None = None
        self._active_workers: set[SearchWorker] = set()
        self._preload_thread: threading.Thread | None = None
        self._unload_thread: threading.Thread | None = None
        self._used_ollama_while_open = False

        self._semantic_timer = QTimer(self)
        self._semantic_timer.setSingleShot(True)
        self._semantic_timer.setInterval(120)
        self._semantic_timer.timeout.connect(self._trigger_async_semantic_search)

        self._build_ui()
        self._set_geometry_collapsed(force=True)
        self.refresh_theme()

    def _build_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.container = QFrame(self)
        self.container.setObjectName("panelContainer")
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        container_layout.addWidget(self._build_header_widget())

        self.content_widget = self._build_content_widget()
        self.content_widget.setVisible(False)
        container_layout.addWidget(self.content_widget, stretch=1)

        outer_layout.addWidget(self.container)

    def _build_header_widget(self) -> QWidget:
        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(14, 8, 14, 8)
        header_layout.setSpacing(8)

        self.search_icon = QLabel("🔍")
        self.search_icon.setFont(get_ui_font(14))

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(tr("Search files, documents, code, media... (prefix 'code:' for source)"))
        self.search_input.setFixedHeight(38)
        self.search_input.setFont(get_ui_font(14))
        self.search_input.textChanged.connect(self._on_query_text_changed)
        self.search_input.installEventFilter(self)

        self.status_badge = QLabel("")
        self.status_badge.setFont(get_ui_font(10, QFont.Weight.Medium))

        header_layout.addWidget(self.search_icon)
        header_layout.addWidget(self.search_input, stretch=1)
        header_layout.addWidget(self.status_badge)
        return header_widget

    def _build_content_widget(self) -> QWidget:
        content_widget = QWidget()
        content_outer = QVBoxLayout(content_widget)
        content_outer.setContentsMargins(0, 0, 0, 0)
        content_outer.setSpacing(0)

        self.divider = QFrame()
        self.divider.setFixedHeight(1)
        content_outer.addWidget(self.divider)

        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.result_list = DraggableResultList()
        self.result_list.setObjectName("panelResultList")
        self.result_list.setFixedWidth(ThemeLayout.LIST_WIDTH)
        self.result_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.result_list.currentItemChanged.connect(self._on_selection_changed)
        self.result_list.itemClicked.connect(self._on_item_clicked)
        self.result_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.result_list.installEventFilter(self)

        self.preview_pane = PreviewPane(config=self.config)
        self.preview_pane.setMinimumWidth(ThemeLayout.PREVIEW_WIDTH)
        self.preview_pane.action_triggered.connect(self.hide_panel)

        content_layout.addWidget(self.result_list)
        content_layout.addWidget(self.preview_pane, stretch=1)
        content_outer.addLayout(content_layout, stretch=1)

        self.footer = QFrame()
        self.footer.setFixedHeight(26)
        footer_layout = QHBoxLayout(self.footer)
        footer_layout.setContentsMargins(12, 0, 8, 0)
        self.footer_hint = QLabel(tr(_FOOTER_HINT_KEY))
        self.footer_hint.setFont(get_ui_font(10))
        footer_layout.addWidget(self.footer_hint)
        footer_layout.addStretch()

        self.size_grip = QSizeGrip(self)
        self.size_grip.setFixedSize(14, 14)
        self.size_grip.setStyleSheet("background: transparent;")
        footer_layout.addWidget(self.size_grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

        content_outer.addWidget(self.footer)
        return content_widget

    def _is_rtl_layout(self) -> bool:
        return self.layoutDirection() == Qt.LayoutDirection.RightToLeft

    def _apply_list_border_style(self) -> None:
        edge = "border-left" if self._is_rtl_layout() else "border-right"
        self.result_list.setStyleSheet(
            f"QListWidget#panelResultList {{ {edge}: 1px solid {get_palette().border}; padding: 4px 6px; }}"
        )

    def _apply_panel_styles(self) -> None:
        p = get_palette()
        self.container.setStyleSheet(
            f"QFrame#panelContainer {{"
            f"  background-color: {p.bg_panel};"
            f"  border: 1.5px solid {p.brand_start};"
            f"  border-radius: {ThemeLayout.PANEL_RADIUS}px;"
            f"}}"
        )
        self.search_icon.setStyleSheet(f"color: {p.text_secondary}; background: transparent; border: none;")
        self.search_input.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none; color: {p.text_primary}; "
            f"placeholder-text-color: {p.text_secondary}; padding: 0px 4px; }}"
        )
        self.status_badge.setStyleSheet(f"color: {p.text_secondary}; background: transparent; border: none;")
        self.divider.setStyleSheet(f"background-color: {p.border}; border: none;")
        self._apply_list_border_style()
        self.footer.setStyleSheet(
            f"background-color: {p.bg_panel_footer}; border-top: 1px solid {p.border}; "
            f"border-bottom-left-radius: {ThemeLayout.PANEL_RADIUS}px; "
            f"border-bottom-right-radius: {ThemeLayout.PANEL_RADIUS}px;"
        )
        self.footer_hint.setStyleSheet(f"color: {p.text_muted}; border: none; background: transparent;")

    def refresh_theme(self) -> None:
        """Apply configured theme across panel chrome, result rows, and preview views."""
        apply_theme(self, self.config.theme)
        self._apply_panel_styles()
        self.preview_pane.empty_view.refresh_theme()
        self.preview_pane.image_view.refresh_theme()
        self.preview_pane.pdf_view.refresh_theme()
        self.preview_pane.audio_view.refresh_theme()
        self.preview_pane.video_view.refresh_theme()
        self.preview_pane.code_view.refresh_theme()
        if self._current_result_set.ranked or self._current_result_set.weaker:
            self._populate_list(self._current_result_set)

    def _update_status_badge(self) -> None:
        if not self._last_query:
            self.status_badge.setText("")
            return
        if self._semantic_timer.isActive() or self._worker is not None:
            self.status_badge.setText(tr("Searching…"))
            return
        total = len(self._current_result_set.ranked) + len(self._current_result_set.weaker)
        self.status_badge.setText(
            tr("{count} matches", count=len(self._current_result_set.ranked)) if total > 0 else tr("No matches")
        )

    def retranslate_ui(self) -> None:
        """Refresh translated panel strings, status badge, and RTL border direction."""
        self._apply_list_border_style()
        self.search_input.setPlaceholderText(tr("Search files, documents, code, media... (prefix 'code:' for source)"))
        self.footer_hint.setText(tr(_FOOTER_HINT_KEY))
        self._update_status_badge()
        self.preview_pane.retranslate_ui()
        if self._current_result_set.ranked or self._current_result_set.weaker:
            self._populate_list(self._current_result_set)

    def _set_geometry_collapsed(self, *, force: bool = False) -> None:
        if not force and not self.is_expanded:
            return
        self.is_expanded = False
        self.content_widget.setVisible(False)
        target_height = ThemeLayout.PANEL_HEIGHT_COLLAPSED
        self.setFixedSize(ThemeLayout.PANEL_WIDTH, target_height)
        self._reposition_to_top_third(target_height)

    @staticmethod
    def _active_screen() -> QScreen | None:
        return QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()

    def _set_geometry_expanded(self, *, force: bool = False) -> None:
        if not force and self.is_expanded:
            return
        self.is_expanded = True
        self.content_widget.setVisible(True)
        screen = self._active_screen()
        target_height = (
            min(ThemeLayout.PANEL_HEIGHT_EXPANDED, screen.availableGeometry().height() - _SCREEN_VERTICAL_MARGIN)
            if screen is not None
            else ThemeLayout.PANEL_HEIGHT_EXPANDED
        )
        self.setMinimumSize(ThemeLayout.PANEL_WIDTH, min(_MIN_EXPANDED_HEIGHT, target_height))
        self.setMaximumSize(_MAX_WIDGET_SIZE, _MAX_WIDGET_SIZE)
        self.resize(max(self.width(), ThemeLayout.PANEL_WIDTH), max(self.height(), target_height))
        self._reposition_to_top_third(target_height)

    def _reposition_to_top_third(self, target_height: int | None = None) -> None:
        screen = self._active_screen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        width = self.width() if self.width() > 0 else ThemeLayout.PANEL_WIDTH
        height = self.height() if target_height is None else target_height
        x = geo.x() + (geo.width() - width) // 2
        y = geo.y() + int(geo.height() * 0.16)
        y = max(geo.y(), min(y, geo.y() + geo.height() - height))
        self.move(x, y)

    def _cleanup_worker(self, worker: SearchWorker) -> None:
        self._active_workers.discard(worker)
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()

    def _stop_workers(self) -> None:
        self._semantic_timer.stop()
        for worker in self._active_workers:
            worker.stop()
        self._worker = None

    def shutdown(self) -> None:
        """Stop timers, wait for all active search workers, and shut down preview pane."""
        self._semantic_timer.stop()
        workers = list(self._active_workers)
        for worker in workers:
            worker.stop()
        for worker in workers:
            worker.wait(_WORKER_SHUTDOWN_WAIT_MS)
        self._active_workers.clear()
        self._worker = None
        self.preview_pane.shutdown()

    def _on_query_text_changed(self, text: str) -> None:
        stripped = text.strip()
        self._last_query = stripped
        if not stripped:
            self._stop_workers()
            self._current_result_set = ResultSet()
            self.status_badge.setText("")
            self.result_list.clear()
            self.preview_pane.clear()
            self._set_geometry_collapsed()
            return

        self.status_badge.setText(tr("Searching…"))
        self._execute_instant_lexical(stripped)
        self._semantic_timer.start(120)

    def _execute_instant_lexical(self, query: str) -> None:
        """Run fast keyword match via SearchService without direct SQLite access."""
        result_set = self.search_service.lexical_search(query, limit=50)
        if result_set.ranked or result_set.weaker:
            self._current_result_set = result_set
            self._populate_list(result_set)
            self._set_geometry_expanded()
        else:
            self.result_list.clear()
            self.preview_pane.clear()
            if not self.is_expanded:
                self._set_geometry_collapsed()

    def _trigger_async_semantic_search(self) -> None:
        """Trigger background worker for semantic search."""
        if not self._last_query:
            return
        self._stop_workers()
        if self.search_service.ollama is not None:
            self._used_ollama_while_open = True
        worker = SearchWorker(self.search_service, self._last_query, limit=50)
        self._active_workers.add(worker)
        self._worker = worker
        worker.results_ready.connect(self._on_semantic_results_ready)
        worker.finished.connect(lambda w=worker: self._cleanup_worker(w))
        worker.start()

    def _on_semantic_results_ready(self, query: str, result_set: ResultSet) -> None:
        """Handle background search results when query matches active query."""
        if query != self._last_query:
            return
        self._apply_semantic_results(result_set)

    def _apply_semantic_results(self, result_set: ResultSet) -> None:
        """Apply semantic search results to UI list and preview."""
        self._current_result_set = result_set
        total = len(result_set.ranked) + len(result_set.weaker)
        self.status_badge.setText(
            tr("{count} matches", count=len(result_set.ranked)) if total > 0 else tr("No matches")
        )
        self._populate_list(result_set)
        if total > 0:
            self._set_geometry_expanded()
        else:
            self.preview_pane.clear()
            self._set_geometry_collapsed()

    def _current_hit(self) -> SearchHit | None:
        if self.result_list.currentRow() < 0:
            return None
        hit = self.result_list.currentItem().data(Qt.ItemDataRole.UserRole)
        return hit if isinstance(hit, SearchHit) else None

    def _add_hit_item(self, hit: SearchHit) -> QListWidgetItem:
        name = Path(hit.path).name
        item = QListWidgetItem()
        item.setSizeHint(QSize(ThemeLayout.LIST_WIDTH - 16, 50))
        item.setData(Qt.ItemDataRole.UserRole, hit)
        item.setData(Qt.ItemDataRole.AccessibleTextRole, f"{name} — {tr(hit.reason)}")
        widget = ResultItemWidget(hit)
        self.result_list.addItem(item)
        self.result_list.setItemWidget(item, widget)
        return item

    def _add_hit_items(self, hits: list[SearchHit], selected_path: str | None) -> QListWidgetItem | None:
        matched_item: QListWidgetItem | None = None
        for hit in hits:
            item = self._add_hit_item(hit)
            if matched_item is None and hit.path == selected_path:
                matched_item = item
        return matched_item

    def _add_ranked_groups(self, ranked: list[SearchHit], selected_path: str | None) -> QListWidgetItem | None:
        grouped: dict[FileKind, list[SearchHit]] = {}
        for hit in ranked:
            grouped.setdefault(hit.kind, []).append(hit)

        restored_item: QListWidgetItem | None = None
        muted_color = QColor(get_palette().text_muted)
        for kind, hits in grouped.items():
            header_item = QListWidgetItem(tr(KIND_GROUP_NAMES.get(kind, "Other")).upper())
            header_item.setFont(get_ui_font(10, QFont.Weight.Bold))
            header_item.setForeground(muted_color)
            header_item.setFlags(Qt.ItemFlag.NoItemFlags)
            header_item.setSizeHint(QSize(ThemeLayout.LIST_WIDTH - 16, 24))
            self.result_list.addItem(header_item)

            group_match = self._add_hit_items(hits, selected_path)
            if restored_item is None:
                restored_item = group_match
        return restored_item

    def _add_weaker_section(self, weaker: list[SearchHit], selected_path: str | None) -> QListWidgetItem | None:
        collapsed_arrow = "◂" if self._is_rtl_layout() else "▸"
        arrow = "▾" if self.weaker_expanded else collapsed_arrow
        toggle_text = tr("{arrow} {count} weaker matches", arrow=arrow, count=len(weaker))
        toggle_item = QListWidgetItem(toggle_text)
        toggle_item.setFont(get_ui_font(11, QFont.Weight.Medium))
        toggle_item.setForeground(QColor(get_palette().brand_start))
        toggle_item.setData(Qt.ItemDataRole.UserRole + 1, "toggle")
        toggle_item.setSizeHint(QSize(ThemeLayout.LIST_WIDTH - 16, 30))
        self.result_list.addItem(toggle_item)
        if not self.weaker_expanded:
            return None
        return self._add_hit_items(weaker, selected_path)

    def _populate_list(self, result_set: ResultSet) -> None:
        selected_hit = self._current_hit()
        selected_path = selected_hit.path if selected_hit is not None else None

        self.result_list.clear()

        restored_item = self._add_ranked_groups(result_set.ranked, selected_path)
        if result_set.weaker:
            weaker_match = self._add_weaker_section(result_set.weaker, selected_path)
            if restored_item is None:
                restored_item = weaker_match

        if restored_item is not None:
            self.result_list.setCurrentItem(restored_item)
        else:
            self._select_first_result()

    def _selectable_rows(self) -> list[int]:
        rows: list[int] = []
        for idx in range(self.result_list.count()):
            item = self.result_list.item(idx)
            if item.data(Qt.ItemDataRole.UserRole) is not None or item.data(Qt.ItemDataRole.UserRole + 1) == "toggle":
                rows.append(idx)
        return rows

    def _select_first_result(self) -> None:
        for idx in range(self.result_list.count()):
            item = self.result_list.item(idx)
            if item.data(Qt.ItemDataRole.UserRole) is not None:
                self.result_list.setCurrentItem(item)
                break

    def _select_boundary_result(self, *, last: bool) -> None:
        rows = self._selectable_rows()
        if rows:
            self.result_list.setCurrentRow(rows[-1] if last else rows[0])

    def _on_selection_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        hit = current.data(Qt.ItemDataRole.UserRole)
        if isinstance(hit, SearchHit):
            self.preview_pane.load_result(hit, query=self.search_input.text())

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        if item.data(Qt.ItemDataRole.UserRole + 1) == "toggle":
            self.weaker_expanded = not self.weaker_expanded
            self._populate_list(self._current_result_set)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        hit = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(hit, SearchHit):
            execute_hit_action(hit, self.config)
            self.hide_panel()

    def _build_quick_actions_menu(self, hit: SearchHit) -> QMenu:
        menu = QMenu(tr("Quick Actions"), self)

        open_action = menu.addAction(tr("Open"))
        open_action.triggered.connect(lambda: self._run_and_hide(lambda: execute_hit_action(hit, self.config)))

        reveal_action = menu.addAction(tr("Show in Files"))
        reveal_action.triggered.connect(lambda: self._run_and_hide(lambda: reveal_in_file_manager(hit.path)))

        menu.addSeparator()

        copy_file_action = menu.addAction(tr("Copy File"))
        copy_file_action.triggered.connect(lambda: copy_file_to_clipboard(hit.path))

        copy_path_action = menu.addAction(tr("Copy Path"))
        copy_path_action.triggered.connect(lambda: copy_to_clipboard(hit.path))

        copy_excerpt_action = menu.addAction(tr("Copy Excerpt"))
        copy_excerpt_action.setEnabled(bool(hit.excerpt))
        copy_excerpt_action.triggered.connect(lambda: copy_to_clipboard(hit.excerpt))

        menu.addSeparator()

        terminal_action = menu.addAction(tr("Open Terminal Here"))
        terminal_action.triggered.connect(
            lambda: self._run_and_hide(
                lambda: open_terminal_at(
                    hit.path,
                    terminal_preference=self.config.terminal,
                    custom_terminal_cmd=self.config.custom_terminal_cmd,
                )
            )
        )
        return menu

    def _run_and_hide(self, action: Callable[[], object]) -> None:
        action()
        self.hide_panel()

    def _show_quick_actions_menu(self) -> bool:
        hit = self._current_hit()
        if hit is None:
            return False
        menu = self._build_quick_actions_menu(hit)
        pos = (
            self.result_list.viewport().mapToGlobal(
                self.result_list.visualItemRect(self.result_list.currentItem()).bottomLeft()
            )
            if self.result_list.currentRow() >= 0
            else self.mapToGlobal(self.rect().center())
        )
        self._menu_open = True
        try:
            menu.exec(pos)
        finally:
            self._menu_open = False
        return True

    def _handle_navigation_key(self, key: int, modifiers: Qt.KeyboardModifier) -> bool:
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self._move_selection(1 if key == Qt.Key.Key_Down else -1)
            return True
        if key in (Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
            self._move_selection(_PAGE_NAV_STEP if key == Qt.Key.Key_PageDown else -_PAGE_NAV_STEP)
            return True
        if key in (Qt.Key.Key_Home, Qt.Key.Key_End) and self.result_list.count() > 0:
            self._select_boundary_result(last=(key == Qt.Key.Key_End))
            return True
        if (modifiers & Qt.KeyboardModifier.AltModifier) and self.preview_pane.currentIndex() == _PDF_PREVIEW_INDEX:
            if key == Qt.Key.Key_Left:
                self.preview_pane.pdf_view.prev_page()
                return True
            if key == Qt.Key.Key_Right:
                self.preview_pane.pdf_view.next_page()
                return True
        return False

    def _handle_action_key(self, key: int, modifiers: Qt.KeyboardModifier) -> bool:
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._activate_current_item(modifiers)
            return True
        if key == Qt.Key.Key_Escape:
            if self.search_input.text():
                self.search_input.clear()
                self._set_geometry_collapsed()
            else:
                self.hide_panel()
            return True
        if key == Qt.Key.Key_C and (modifiers & Qt.KeyboardModifier.ControlModifier):
            if self.search_input.hasSelectedText():
                return False
            self._copy_selected_path()
            return True
        if key == Qt.Key.Key_Tab or (key == Qt.Key.Key_K and (modifiers & Qt.KeyboardModifier.ControlModifier)):
            return self._show_quick_actions_menu()
        return False

    def _handle_key_press(self, key_event: QKeyEvent) -> bool:
        key = key_event.key()
        modifiers = key_event.modifiers()
        return self._handle_navigation_key(key, modifiers) or self._handle_action_key(key, modifiers)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Handle keyboard navigation across search input and results list."""
        if isinstance(event, QKeyEvent) and event.type() == QEvent.Type.KeyPress and self._handle_key_press(event):
            return True
        return super().eventFilter(watched, event)

    def _move_selection(self, step: int) -> None:
        rows = self._selectable_rows()
        if not rows:
            return
        cur_row = self.result_list.currentRow()
        if cur_row in rows:
            target_pos = max(0, min(len(rows) - 1, rows.index(cur_row) + step))
            self.result_list.setCurrentRow(rows[target_pos])
        else:
            self.result_list.setCurrentRow(rows[0] if step > 0 else rows[-1])

    def _activate_current_item(self, modifiers: Qt.KeyboardModifier) -> None:
        if self.result_list.currentRow() < 0:
            return
        current = self.result_list.currentItem()

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
        hit = self._current_hit()
        if hit is not None:
            copy_file_to_clipboard(hit.path)

    def execute_search(self, query: str) -> None:
        """Trigger search programmatically."""
        self.search_input.setText(query)

    def _preload_model(self) -> None:
        if self.search_service.ollama is not None:
            with contextlib.suppress(OllamaError):
                self.search_service.ollama.preload(self.search_service.model)

    def _unload_model(self) -> None:
        if self.search_service.ollama is not None:
            with contextlib.suppress(OllamaError):
                self.search_service.ollama.unload(self.search_service.model)

    def _maybe_start_preload(self) -> None:
        if self.search_service.ollama is not None and (
            self._preload_thread is None or not self._preload_thread.is_alive()
        ):
            self._used_ollama_while_open = True
            self._preload_thread = threading.Thread(target=self._preload_model, daemon=True)
            self._preload_thread.start()

    def _maybe_unload_model(self) -> None:
        should_unload = (
            self._used_ollama_while_open
            and self.config.ollama_keep_alive == "unload_idle"
            and self.search_service.ollama is not None
            and (self._should_unload_on_hide is None or self._should_unload_on_hide())
        )
        self._used_ollama_while_open = False
        if should_unload and (self._unload_thread is None or not self._unload_thread.is_alive()):
            self._unload_thread = threading.Thread(target=self._unload_model, daemon=True)
            self._unload_thread.start()

    def show_panel(self) -> None:
        """Present search panel with focus."""
        self._stop_workers()
        preserve_query = self.config.remember_last_query and bool(self._last_query)
        if preserve_query:
            if self._current_result_set.ranked or self._current_result_set.weaker:
                self._set_geometry_expanded()
            else:
                self._set_geometry_collapsed(force=True)
        else:
            self.search_input.clear()
            self.result_list.clear()
            self.preview_pane.clear()
            self._set_geometry_collapsed(force=True)
        self.show()
        self.raise_()
        self.activateWindow()
        self.search_input.setFocus()
        if preserve_query:
            self.search_input.selectAll()
        self._maybe_start_preload()

    def hide_panel(self) -> None:
        """Dismiss search panel and release UI/database buffers."""
        self._stop_workers()
        if not self.config.remember_last_query:
            with QSignalBlocker(self.search_input):
                self.search_input.clear()
            self._last_query = ""
            self._current_result_set = ResultSet()
            self.status_badge.setText("")
            self.result_list.clear()
            self.preview_pane.clear()
            self._set_geometry_collapsed()
        self.search_service.db.shrink_memory()
        if self.search_service.code_db is not None:
            self.search_service.code_db.shrink_memory()
        self._maybe_unload_model()
        self.hide()

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.LayoutDirectionChange:
            self._apply_list_border_style()
        elif (
            event.type() == QEvent.Type.WindowDeactivate
            and self.config.close_on_focus_loss
            and self.isVisible()
            and not self._menu_open
            and QApplication.activePopupWidget() is None
        ):
            self.hide_panel()
        return super().event(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.shutdown()
        super().closeEvent(event)
