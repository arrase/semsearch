"""Quick search floating panel with instant lexical FTS5, debounced semantic ranking, and preview."""

import contextlib
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, QSize, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QColor, QFont, QKeyEvent
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
from semsearch.core.database import Database, FtsMatchRow
from semsearch.core.ollama import OllamaError
from semsearch.core.search_text import extract_terms, make_fts_patterns
from semsearch.core.types import FileKind, SearchHit
from semsearch.gui.preview import PreviewPane, format_tilde_path
from semsearch.gui.theme import ThemeColors, ThemeLayout, apply_theme, get_ui_font
from semsearch.gui.widgets import ElidedLabel
from semsearch.gui.worker import SearchWorker
from semsearch.i18n import tr
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

        self.title_label = ElidedLabel(Path(hit.path).name)
        self.title_label.setFont(get_ui_font(12, QFont.Weight.DemiBold))
        self.title_label.setStyleSheet(f"color: {ThemeColors.TEXT_PRIMARY}; background: transparent; border: none;")
        top_row.addWidget(self.title_label, stretch=1)

        place_text = format_hit_place_badge(hit)
        if place_text:
            self.place_badge = QLabel(place_text)
            self.place_badge.setFont(get_ui_font(10, QFont.Weight.Medium))
            self.place_badge.setStyleSheet(
                f"color: {ThemeColors.TEXT_SECONDARY}; background-color: rgba(255, 255, 255, 0.06); "
                "border-radius: 4px; padding: 1px 5px;"
            )
            top_row.addWidget(self.place_badge)

        self.path_label = ElidedLabel(format_tilde_path(str(Path(hit.path).parent)))
        self.path_label.setFont(get_ui_font(10))
        self.path_label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; background: transparent; border: none;")

        text_layout.addLayout(top_row)
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

    def __init__(
        self,
        search_service: SearchService,
        app_config: AppConfig | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.search_service = search_service
        self.config = app_config or AppConfig()

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.is_expanded = False
        self.weaker_expanded = False
        self._current_result_set: ResultSet = ResultSet()
        self._last_query = ""
        self._worker: SearchWorker | None = None
        self._active_workers: set[SearchWorker] = set()
        self._preload_thread: threading.Thread | None = None

        self._semantic_timer = QTimer(self)
        self._semantic_timer.setSingleShot(True)
        self._semantic_timer.setInterval(120)
        self._semantic_timer.timeout.connect(self._trigger_async_semantic_search)

        self._build_ui()
        self._set_geometry_collapsed()
        apply_theme(self)

    def _build_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.container = QFrame(self)
        self.container.setObjectName("panelContainer")
        self.container.setStyleSheet(
            f"QFrame#panelContainer {{"
            f"  background-color: {ThemeColors.BG_PANEL};"
            f"  border: 1.5px solid {ThemeColors.BRAND_START};"
            f"  border-radius: {ThemeLayout.PANEL_RADIUS}px;"
            f"}}"
        )
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
        self.search_icon.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY}; background: transparent; border: none;")

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(tr("Search files, documents, code, media... (prefix 'code:' for source)"))
        self.search_input.setFixedHeight(38)
        self.search_input.setFont(get_ui_font(14))
        self.search_input.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none; color: {ThemeColors.TEXT_PRIMARY}; "
            f"placeholder-text-color: {ThemeColors.TEXT_SECONDARY}; padding: 0px 4px; }}"
        )
        self.search_input.textChanged.connect(self._on_query_text_changed)
        self.search_input.installEventFilter(self)

        self.status_badge = QLabel("")
        self.status_badge.setFont(get_ui_font(10, QFont.Weight.Medium))
        self.status_badge.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY}; background: transparent; border: none;")

        header_layout.addWidget(self.search_icon)
        header_layout.addWidget(self.search_input, stretch=1)
        header_layout.addWidget(self.status_badge)
        return header_widget

    def _build_content_widget(self) -> QWidget:
        content_widget = QWidget()
        content_outer = QVBoxLayout(content_widget)
        content_outer.setContentsMargins(0, 0, 0, 0)
        content_outer.setSpacing(0)

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background-color: {ThemeColors.BORDER}; border: none;")
        content_outer.addWidget(divider)

        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.result_list = QListWidget()
        self.result_list.setObjectName("panelResultList")
        self.result_list.setFixedWidth(ThemeLayout.LIST_WIDTH)
        self.result_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.result_list.setStyleSheet(
            f"QListWidget#panelResultList {{ border-right: 1px solid {ThemeColors.BORDER}; padding: 4px 6px; }}"
        )
        self.result_list.currentItemChanged.connect(self._on_selection_changed)
        self.result_list.itemClicked.connect(self._on_item_clicked)
        self.result_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.result_list.installEventFilter(self)

        self.preview_pane = PreviewPane()
        self.preview_pane.setFixedWidth(ThemeLayout.PREVIEW_WIDTH - 2)
        self.preview_pane.action_triggered.connect(self.hide_panel)

        content_layout.addWidget(self.result_list)
        content_layout.addWidget(self.preview_pane, stretch=1)
        content_outer.addLayout(content_layout, stretch=1)

        footer = QFrame()
        footer.setFixedHeight(26)
        footer.setStyleSheet(
            f"background-color: rgba(15, 17, 21, 0.55); border-top: 1px solid {ThemeColors.BORDER}; "
            f"border-bottom-left-radius: {ThemeLayout.PANEL_RADIUS}px; "
            f"border-bottom-right-radius: {ThemeLayout.PANEL_RADIUS}px;"
        )
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(12, 0, 12, 0)
        self.footer_hint = QLabel(tr("↵ Open    ⇧↵ Show in Files    ⌃C Copy    Esc Dismiss"))
        self.footer_hint.setFont(get_ui_font(10))
        self.footer_hint.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; border: none; background: transparent;")
        footer_layout.addWidget(self.footer_hint)
        footer_layout.addStretch()

        content_outer.addWidget(footer)
        return content_widget

    def retranslate_ui(self) -> None:
        """Refresh translated panel strings."""
        self.search_input.setPlaceholderText(tr("Search files, documents, code, media... (prefix 'code:' for source)"))
        self.footer_hint.setText(tr("↵ Open    ⇧↵ Show in Files    ⌃C Copy    Esc Dismiss"))
        self.preview_pane.retranslate_ui()
        if self._current_result_set.ranked or self._current_result_set.weaker:
            self._populate_list(self._current_result_set)

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

    def _cleanup_worker(self, worker: SearchWorker) -> None:
        self._active_workers.discard(worker)
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()

    def _stop_workers(self) -> None:
        self._semantic_timer.stop()
        for worker in self._active_workers:
            worker.stop()
        if self._worker is not None:
            self._worker.stop()
            self._worker.wait(50)
            self._worker = None

    def _on_query_text_changed(self, text: str) -> None:
        stripped = text.strip()
        self._last_query = stripped
        if not stripped:
            self._stop_workers()
            self.status_badge.setText("")
            self.result_list.clear()
            self.preview_pane.clear()
            self._set_geometry_collapsed()
            return

        self.status_badge.setText(tr("Searching…"))
        self._execute_instant_lexical(stripped)
        self._semantic_timer.start(120)

    @staticmethod
    def _fts_rows_to_hits(fts_rows: list[FtsMatchRow], target_db: Database) -> list[SearchHit]:
        best_by_file: dict[int, SearchHit] = {}
        for r in fts_rows:
            raw_fid = r["file_id"]
            if raw_fid is None:
                continue
            fid = int(raw_fid)
            score = float(-r["rank"])
            if fid in best_by_file and best_by_file[fid].score >= score:
                continue
            file_rec = target_db.find_by_id(fid)
            if not file_rec:
                continue
            raw_size = getattr(file_rec, "size", 0)
            raw_mtime = getattr(file_rec, "mtime", 0.0)
            best_by_file[fid] = SearchHit(
                path=file_rec.path,
                kind=file_rec.kind,
                score=score,
                excerpt=r["snippet"] or r["excerpt"],
                keyword_rank=max(1, int(score)) if score > 0 else 0,
                segment_id=r["segment_id"],
                loc=r["loc"],
                size=raw_size if isinstance(raw_size, int) else 0,
                modified=float(raw_mtime) if isinstance(raw_mtime, (int, float)) else 0.0,
            )
        return list(best_by_file.values())

    def _execute_instant_lexical(self, query: str) -> None:
        """Run fast keyword match against SQLite index, deduplicating by file_id."""
        is_code = query.lower().startswith("code:")
        target_db = self.search_service.code_db if is_code and self.search_service.code_db else self.search_service.db
        clean_q = query[5:].strip() if is_code else query

        fts_rows = target_db.fts_search(clean_q, limit=50)
        if not fts_rows:
            terms = extract_terms(clean_q)
            if terms:
                fts_q = " OR ".join(make_fts_patterns(terms, prefix_last=True))
                fts_rows = target_db.fts_search(fts_q, limit=50)

        hits = self._fts_rows_to_hits(fts_rows, target_db)
        if hits:
            hits.sort(key=lambda h: h.score, reverse=True)
            threshold = 0.5 * hits[0].score
            ranked = [h for h in hits if h.score >= threshold]
            weaker = [h for h in hits if h.score < threshold]
            self._current_result_set = ResultSet(ranked=ranked, weaker=weaker)
            self._populate_list(self._current_result_set)
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

    def _populate_list(self, result_set: ResultSet) -> None:
        self.result_list.clear()

        grouped: dict[FileKind, list[SearchHit]] = {}
        for hit in result_set.ranked:
            grouped.setdefault(hit.kind, []).append(hit)

        for kind, hits in grouped.items():
            header_item = QListWidgetItem(tr(KIND_GROUP_NAMES.get(kind, "Other")).upper())
            header_item.setFont(get_ui_font(10, QFont.Weight.Bold))
            header_item.setForeground(QColor(ThemeColors.TEXT_MUTED))
            header_item.setFlags(Qt.ItemFlag.NoItemFlags)
            header_item.setSizeHint(QSize(ThemeLayout.LIST_WIDTH - 16, 24))
            self.result_list.addItem(header_item)

            for hit in hits:
                item = QListWidgetItem()
                item.setSizeHint(QSize(ThemeLayout.LIST_WIDTH - 16, 50))
                item.setData(Qt.ItemDataRole.UserRole, hit)
                widget = ResultItemWidget(hit)
                self.result_list.addItem(item)
                self.result_list.setItemWidget(item, widget)

        if result_set.weaker:
            arrow = "▾" if self.weaker_expanded else "▸"
            toggle_text = tr("{arrow} {count} weaker matches", arrow=arrow, count=len(result_set.weaker))
            toggle_item = QListWidgetItem(toggle_text)
            toggle_item.setFont(get_ui_font(11, QFont.Weight.Medium))
            toggle_item.setForeground(QColor(ThemeColors.BRAND_START))
            toggle_item.setData(Qt.ItemDataRole.UserRole + 1, "toggle")
            toggle_item.setSizeHint(QSize(ThemeLayout.LIST_WIDTH - 16, 30))
            self.result_list.addItem(toggle_item)

            if self.weaker_expanded:
                for hit in result_set.weaker:
                    item = QListWidgetItem()
                    item.setSizeHint(QSize(ThemeLayout.LIST_WIDTH - 16, 50))
                    item.setData(Qt.ItemDataRole.UserRole, hit)
                    widget = ResultItemWidget(hit)
                    self.result_list.addItem(item)
                    self.result_list.setItemWidget(item, widget)

        self._select_first_result()

    def _select_first_result(self) -> None:
        for idx in range(self.result_list.count()):
            item = self.result_list.item(idx)
            if item.data(Qt.ItemDataRole.UserRole) is not None:
                self.result_list.setCurrentItem(item)
                break

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

    def _handle_key_press(self, key_event: QKeyEvent) -> bool:
        key = key_event.key()
        modifiers = key_event.modifiers()

        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self._move_selection(1 if key == Qt.Key.Key_Down else -1)
            return True
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
            self._copy_selected_path()
            return True
        return False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Handle keyboard navigation across search input and results list."""
        if isinstance(event, QKeyEvent) and event.type() == QEvent.Type.KeyPress and self._handle_key_press(event):
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
        if self.result_list.currentRow() >= 0:
            current = self.result_list.currentItem()
            hit = current.data(Qt.ItemDataRole.UserRole)
            if isinstance(hit, SearchHit):
                copy_file_to_clipboard(hit.path)

    def execute_search(self, query: str) -> None:
        """Trigger search programmatically."""
        self.search_input.setText(query)

    def _preload_model(self) -> None:
        if self.search_service.ollama is not None:
            with contextlib.suppress(OllamaError):
                self.search_service.ollama.preload(self.search_service.model)

    def show_panel(self) -> None:
        """Present search panel with focus."""
        self._stop_workers()
        self.search_input.clear()
        self.result_list.clear()
        self.preview_pane.clear()
        self._set_geometry_collapsed()
        self.show()
        self.raise_()
        self.activateWindow()
        self.search_input.setFocus()
        if self.search_service.ollama is not None and (
            self._preload_thread is None or not self._preload_thread.is_alive()
        ):
            self._preload_thread = threading.Thread(target=self._preload_model, daemon=True)
            self._preload_thread.start()

    def hide_panel(self) -> None:
        """Dismiss search panel and release UI/database buffers."""
        self._stop_workers()
        with QSignalBlocker(self.search_input):
            self.search_input.clear()
        self._last_query = ""
        self._current_result_set = ResultSet()
        self.status_badge.setText("")
        self.result_list.clear()
        self.preview_pane.clear()
        self._set_geometry_collapsed()
        if self.search_service.db is not None:
            self.search_service.db.shrink_memory()
        if self.search_service.code_db is not None:
            self.search_service.code_db.shrink_memory()
        self.hide()

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.WindowDeactivate and self.isVisible():
            self.hide_panel()
        return super().event(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._stop_workers()
        super().closeEvent(event)
