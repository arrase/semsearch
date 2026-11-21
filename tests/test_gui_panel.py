"""Unit and widget tests for quick search panel, UX shortcuts, DnD, RTL, and concurrency."""

import gc
import threading
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, create_autospec, patch

import pytest
from PySide6.QtCore import QEvent, QRect, Qt, QUrl
from PySide6.QtGui import QKeyEvent, QScreen
from PySide6.QtWidgets import QApplication, QMenu

from semsearch.config import AppConfig
from semsearch.core.database import Database
from semsearch.core.ollama import OllamaClient, OllamaConnectionError
from semsearch.core.types import (
    Candidate,
    FileKind,
    MatchReason,
    Modality,
    SearchHit,
    SegmentKind,
)
from semsearch.gui.panel import DraggableResultList, ResultItemWidget, SearchPanel
from semsearch.gui.theme import ThemeColors, ThemeLayout
from semsearch.i18n import load_catalog, set_language
from semsearch.services.searcher import ResultSet, SearchService


@pytest.fixture
def mock_search_service() -> MagicMock:
    service = cast(MagicMock, create_autospec(SearchService, instance=True))
    service.db = create_autospec(Database, instance=True)
    service.db.path = "/tmp/test.db"
    service.code_db = None
    service.ollama = create_autospec(OllamaClient, instance=True)
    service.model = "embeddinggemma-2"

    lexical_hit = SearchHit(
        path="/tmp/sample.py",
        kind=FileKind.CODE,
        score=4.0,
        excerpt="Sample code snippet",
        keyword_rank=1,
        segment_id=10,
        loc=5.0,
    )
    service.lexical_search.return_value = ResultSet(ranked=[lexical_hit])

    hit1 = SearchHit(path="/tmp/doc.pdf", kind=FileKind.PDF, score=1.0, z=3.0, excerpt="Important excerpt")
    hit_weaker = SearchHit(path="/tmp/old.txt", kind=FileKind.DOC, score=0.2)
    service.search.return_value = ResultSet(ranked=[hit1], weaker=[hit_weaker])
    return service


def test_panel_geometry_and_flags(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify window flags, initial collapsed geometry, preview width, and contrast styling."""
    del qapp
    panel = SearchPanel(mock_search_service, AppConfig())
    flags = panel.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert panel.width() == ThemeLayout.PANEL_WIDTH
    assert panel.height() == ThemeLayout.PANEL_HEIGHT_COLLAPSED
    assert panel.result_list.width() == ThemeLayout.LIST_WIDTH
    assert panel.preview_pane.width() == ThemeLayout.PREVIEW_WIDTH
    assert isinstance(panel.result_list, DraggableResultList)
    assert not panel.is_expanded
    assert f"background-color: {ThemeColors.BG_PANEL};" in panel.container.styleSheet()
    assert f"border: 1.5px solid {ThemeColors.BRAND_START};" in panel.container.styleSheet()
    assert f"border-radius: {ThemeLayout.PANEL_RADIUS}px;" in panel.container.styleSheet()
    assert ThemeColors.TEXT_SECONDARY in panel.search_icon.styleSheet()
    assert f"placeholder-text-color: {ThemeColors.TEXT_SECONDARY};" in panel.search_input.styleSheet()
    assert ThemeColors.TEXT_SECONDARY in panel.status_badge.styleSheet()
    panel.shutdown()


def test_panel_expansion_and_no_redundant_resize_while_expanded(
    mock_search_service: MagicMock, qapp: QApplication
) -> None:
    """Verify panel expands once on query matches and avoids redundant setFixedSize on subsequent keystrokes."""
    del qapp
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.show()

    with patch.object(panel, "setFixedSize", wraps=panel.setFixedSize) as mock_resize:
        panel.search_input.setText("sam")
        assert bool(panel.is_expanded)
        assert panel.height() == ThemeLayout.PANEL_HEIGHT_EXPANDED
        assert panel.content_widget.isVisible()
        assert panel.result_list.count() > 0
        assert mock_resize.call_count == 1
        mock_search_service.lexical_search.assert_called_with("sam", limit=50)

        # Typing another character while already expanded must not call setFixedSize again
        panel.search_input.setText("sample")
        assert bool(panel.is_expanded)
        assert mock_resize.call_count == 1

        # Clearing text collapses once
        panel.search_input.setText("")
        assert not bool(panel.is_expanded)
        assert panel.height() == ThemeLayout.PANEL_HEIGHT_COLLAPSED
        assert not panel.content_widget.isVisible()
        assert mock_resize.call_count == 2
    panel.shutdown()


def test_panel_small_screen_clamps_height_and_y(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify expanded height and vertical placement clamp to small screen availableGeometry."""
    del qapp
    small_screen = create_autospec(QScreen, instance=True)
    small_screen.availableGeometry.return_value = QRect(100, 50, 1024, 400)

    panel = SearchPanel(mock_search_service, AppConfig())
    with patch.object(panel, "_active_screen", return_value=small_screen):
        panel.search_input.setText("sample")
        assert panel.is_expanded
        assert panel.height() == 360
        assert panel.y() >= 50
        assert panel.y() + panel.height() <= 450
    panel.shutdown()


def test_panel_semantic_debounce_timer_passes_prefix_last(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify semantic debounce timer triggers hybrid search with prefix_last=True."""
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.search_input.setText("hello")
    assert panel._semantic_timer.isActive()

    panel._semantic_timer.timeout.emit()
    assert panel._worker is not None
    panel._worker.wait()
    qapp.processEvents()
    mock_search_service.search.assert_called_with("hello", limit=50, is_code=False, prefix_last=True)
    panel.shutdown()


def test_weaker_matches_folding_toggle(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify folding toggle row expands weaker matches."""
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.search_input.setText("doc")
    panel._semantic_timer.timeout.emit()
    assert panel._worker is not None
    panel._worker.wait()
    qapp.processEvents()

    toggle_row = -1
    for idx in range(panel.result_list.count()):
        item = panel.result_list.item(idx)
        if item.data(Qt.ItemDataRole.UserRole + 1) == "toggle":
            toggle_row = idx
            break

    assert toggle_row != -1
    initial_count = panel.result_list.count()

    panel._on_item_clicked(panel.result_list.item(toggle_row))
    assert panel.weaker_expanded
    assert panel.result_list.count() > initial_count
    panel.shutdown()


def test_panel_preserves_selection_across_async_update_and_weaker_toggle(
    mock_search_service: MagicMock, qapp: QApplication
) -> None:
    """Verify selected hit.path stays selected when semantic results arrive or weaker matches toggle."""
    del qapp
    hit_a = SearchHit(path="/tmp/alpha.pdf", kind=FileKind.PDF, score=2.0)
    hit_b = SearchHit(path="/tmp/beta.pdf", kind=FileKind.PDF, score=1.5)
    hit_c = SearchHit(path="/tmp/gamma.txt", kind=FileKind.DOC, score=0.3)
    mock_search_service.lexical_search.return_value = ResultSet(ranked=[hit_a, hit_b])

    panel = SearchPanel(mock_search_service, AppConfig())
    panel.search_input.setText("report")

    # Move selection from alpha.pdf to beta.pdf
    panel.eventFilter(
        panel.search_input, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
    )
    selected = panel._current_hit()
    assert selected is not None
    assert selected.path == "/tmp/beta.pdf"

    # Async semantic results arrive with updated hit scores; beta.pdf must remain selected
    updated_b = SearchHit(path="/tmp/beta.pdf", kind=FileKind.PDF, score=3.1, z=2.9)
    panel._on_semantic_results_ready("report", ResultSet(ranked=[hit_a, updated_b], weaker=[hit_c]))
    selected_after = panel._current_hit()
    assert selected_after is not None
    assert selected_after.path == "/tmp/beta.pdf"
    assert selected_after.z == pytest.approx(2.9)

    # Toggling weaker matches preserves beta.pdf selection
    toggle_item = next(
        panel.result_list.item(i)
        for i in range(panel.result_list.count())
        if panel.result_list.item(i).data(Qt.ItemDataRole.UserRole + 1) == "toggle"
    )
    panel._on_item_clicked(toggle_item)
    selected_after_toggle = panel._current_hit()
    assert selected_after_toggle is not None
    assert selected_after_toggle.path == "/tmp/beta.pdf"

    # If previously selected path disappears, selection falls back to first result
    hit_d = SearchHit(path="/tmp/delta.pdf", kind=FileKind.PDF, score=2.5)
    panel._on_semantic_results_ready("report", ResultSet(ranked=[hit_d]))
    fallback = panel._current_hit()
    assert fallback is not None
    assert fallback.path == "/tmp/delta.pdf"
    panel.shutdown()


def test_keyboard_navigation_and_actions(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify arrows, Enter, Shift+Enter, Ctrl+C (with/without input selection), and Escape."""
    panel = SearchPanel(mock_search_service, AppConfig())
    assert panel.footer_hint.text() == "↵ Open    Shift+↵ Show in Files    Ctrl+C Copy    Tab Actions    Esc Dismiss"
    panel.search_input.setText("doc")
    panel._semantic_timer.timeout.emit()
    assert panel._worker is not None
    panel._worker.wait()
    qapp.processEvents()

    event_down = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
    panel.eventFilter(panel.search_input, event_down)
    assert panel.result_list.currentRow() >= 0

    hit_row = next(
        idx
        for idx in range(panel.result_list.count())
        if isinstance(panel.result_list.item(idx).data(Qt.ItemDataRole.UserRole), SearchHit)
    )
    panel.result_list.setCurrentRow(hit_row)

    # 1. Enter (execute_hit_action)
    with patch("semsearch.gui.panel.execute_hit_action") as mock_exec, patch.object(panel, "hide_panel") as mock_hide:
        event_enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
        panel.eventFilter(panel.search_input, event_enter)
        mock_exec.assert_called_once()
        mock_hide.assert_called_once()

    # 2. Shift+Enter (reveal_in_file_manager)
    with (
        patch("semsearch.gui.panel.reveal_in_file_manager") as mock_reveal,
        patch.object(panel, "hide_panel") as mock_hide,
    ):
        event_shift_enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        panel.eventFilter(panel.search_input, event_shift_enter)
        mock_reveal.assert_called_once()
        mock_hide.assert_called_once()

    # 3. Ctrl+C with selected text in search_input lets Qt copy input text instead of file
    panel.search_input.selectAll()
    assert panel.search_input.hasSelectedText()
    with patch("semsearch.gui.panel.copy_file_to_clipboard") as mock_copy:
        event_ctrl_c = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        handled = panel.eventFilter(panel.search_input, event_ctrl_c)
        assert handled is False
        mock_copy.assert_not_called()

    # 4. Ctrl+C without text selection copies the selected result file
    panel.search_input.deselect()
    with patch("semsearch.gui.panel.copy_file_to_clipboard") as mock_copy:
        event_ctrl_c = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        handled = panel.eventFilter(panel.search_input, event_ctrl_c)
        assert handled is True
        mock_copy.assert_called_once_with("/tmp/doc.pdf")

    # 5. Escape with text clears input; Escape with empty input hides panel
    event_esc = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    panel.eventFilter(panel.search_input, event_esc)
    assert panel.search_input.text() == ""
    assert not panel.is_expanded

    with patch.object(panel, "hide_panel") as mock_hide:
        panel.eventFilter(panel.search_input, event_esc)
        mock_hide.assert_called_once()
    panel.shutdown()


def test_extended_keyboard_navigation_and_pdf_paging(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify Home, End, PageDown, PageUp result navigation and Alt+Left/Right PDF paging."""
    del qapp
    hits = [SearchHit(path=f"/tmp/file_{i}.pdf", kind=FileKind.PDF, score=float(10 - i)) for i in range(7)]
    mock_search_service.lexical_search.return_value = ResultSet(ranked=hits)

    panel = SearchPanel(mock_search_service, AppConfig())
    panel.search_input.setText("file")

    # End selects last hit
    panel.eventFilter(
        panel.search_input, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_End, Qt.KeyboardModifier.NoModifier)
    )
    assert panel._current_hit() == hits[-1]

    # Home selects first hit
    panel.eventFilter(
        panel.search_input, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Home, Qt.KeyboardModifier.NoModifier)
    )
    assert panel._current_hit() == hits[0]

    # PageDown moves forward 5 items; PageUp moves back to 0
    panel.eventFilter(
        panel.search_input, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_PageDown, Qt.KeyboardModifier.NoModifier)
    )
    assert panel._current_hit() == hits[5]

    panel.eventFilter(
        panel.search_input, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_PageUp, Qt.KeyboardModifier.NoModifier)
    )
    assert panel._current_hit() == hits[0]

    # Alt+Right / Alt+Left navigate PDF pages when PDF preview is active
    panel.preview_pane.setCurrentIndex(2)
    with (
        patch.object(panel.preview_pane.pdf_view, "next_page") as mock_next,
        patch.object(panel.preview_pane.pdf_view, "prev_page") as mock_prev,
    ):
        panel.eventFilter(
            panel.search_input, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
        )
        panel.eventFilter(
            panel.search_input, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
        )
        mock_next.assert_called_once()
        mock_prev.assert_called_once()
    panel.shutdown()


def test_quick_actions_menu_and_deactivate_guard(
    mock_search_service: MagicMock, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify Tab/Ctrl+K opens Quick Actions menu with all 6 actions and guards WindowDeactivate."""
    del qapp
    hit = SearchHit(path="/tmp/notes.md", kind=FileKind.DOC, score=2.0, excerpt="Matched passage")
    mock_search_service.lexical_search.return_value = ResultSet(ranked=[hit])

    panel = SearchPanel(mock_search_service, AppConfig())
    panel.show_panel()
    panel.search_input.setText("notes")

    # Verify all 6 actions and their handlers
    menu = panel._build_quick_actions_menu(hit)
    actions = {act.text(): act for act in menu.actions() if not act.isSeparator()}
    assert list(actions.keys()) == [
        "Open",
        "Show in Files",
        "Copy File",
        "Copy Path",
        "Copy Excerpt",
        "Open Terminal Here",
    ]

    with (
        patch("semsearch.gui.panel.execute_hit_action") as mock_open,
        patch("semsearch.gui.panel.reveal_in_file_manager") as mock_reveal,
        patch("semsearch.gui.panel.copy_file_to_clipboard") as mock_copy_file,
        patch("semsearch.gui.panel.copy_to_clipboard") as mock_copy_text,
        patch("semsearch.gui.panel.open_terminal_at") as mock_term,
        patch.object(panel, "hide_panel") as mock_hide,
    ):
        actions["Open"].trigger()
        mock_open.assert_called_once_with(hit, panel.config)
        actions["Show in Files"].trigger()
        mock_reveal.assert_called_once_with("/tmp/notes.md")
        actions["Copy File"].trigger()
        mock_copy_file.assert_called_once_with("/tmp/notes.md")
        actions["Copy Path"].trigger()
        actions["Copy Excerpt"].trigger()
        assert mock_copy_text.call_args_list[0].args == ("/tmp/notes.md",)
        assert mock_copy_text.call_args_list[1].args == ("Matched passage",)
        actions["Open Terminal Here"].trigger()
        mock_term.assert_called_once_with("/tmp/notes.md")
        assert mock_hide.call_count == 3

    # Tab and Ctrl+K invoke menu.exec while _menu_open guards WindowDeactivate
    exec_calls: list[object] = []
    orig_build = panel._build_quick_actions_menu

    def fake_exec(pos: object) -> None:
        assert bool(panel._menu_open)
        QApplication.sendEvent(panel, QEvent(QEvent.Type.WindowDeactivate))
        assert panel.isVisible()
        exec_calls.append(pos)

    def build_patched_menu(target_hit: SearchHit) -> QMenu:
        built = orig_build(target_hit)
        monkeypatch.setattr(built, "exec", fake_exec)
        return built

    monkeypatch.setattr(panel, "_build_quick_actions_menu", build_patched_menu)

    assert panel.eventFilter(
        panel.search_input, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier)
    )
    assert len(exec_calls) == 1
    assert not bool(panel._menu_open)
    assert panel.eventFilter(
        panel.search_input, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    )
    assert len(exec_calls) == 2
    panel.shutdown()


def test_draggable_result_list_mime_data(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify DraggableResultList produces local file QUrl QMimeData for hits and None for headers."""
    del qapp
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.search_input.setText("sample")

    assert panel.result_list.dragEnabled()
    header_item = panel.result_list.item(0)
    hit_item = panel.result_list.item(1)

    assert panel.result_list.build_mime_data(header_item) is None
    mime = panel.result_list.build_mime_data(hit_item)
    assert mime is not None
    assert mime.urls() == [QUrl.fromLocalFile("/tmp/sample.py")]
    assert mime.text() == "/tmp/sample.py"
    panel.shutdown()


def test_accessibility_reason_translation_and_rtl_layout(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify AccessibleTextRole, translated ResultItemWidget reason, status_badge retranslation, and RTL visuals."""
    del qapp
    hit = SearchHit(path="/tmp/design.pdf", kind=FileKind.PDF, score=2.0, z=3.2)
    weaker = SearchHit(path="/tmp/old.txt", kind=FileKind.DOC, score=0.2)
    mock_search_service.lexical_search.return_value = ResultSet(ranked=[hit], weaker=[weaker])

    set_language("es")
    es_catalog = load_catalog("es")
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.search_input.setText("design")
    panel._apply_semantic_results(ResultSet(ranked=[hit], weaker=[weaker]))
    panel._semantic_timer.stop()

    hit_item = panel.result_list.item(1)
    assert hit_item.data(Qt.ItemDataRole.AccessibleTextRole) == f"design.pdf — {es_catalog[MatchReason.MEANING]}"
    row_widget = panel.result_list.itemWidget(hit_item)
    assert isinstance(row_widget, ResultItemWidget)
    assert row_widget.reason_label.text() == es_catalog[MatchReason.MEANING]

    # Switch to RTL and retranslate
    set_language("ar")
    ar_catalog = load_catalog("ar")
    panel.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    panel.retranslate_ui()

    assert "border-left:" in panel.result_list.styleSheet()
    assert "border-right:" not in panel.result_list.styleSheet()
    assert panel.status_badge.text() == ar_catalog["{count} matches"].format(count=1)
    toggle_item = next(
        panel.result_list.item(i)
        for i in range(panel.result_list.count())
        if panel.result_list.item(i).data(Qt.ItemDataRole.UserRole + 1) == "toggle"
    )
    assert "◂" in toggle_item.text()
    panel.shutdown()


def test_panel_instant_lexical_with_real_database(qapp: QApplication, tmp_path: Path) -> None:
    """Verify instant lexical search delegates to real SearchService.lexical_search against SQLite FTS5."""
    del qapp
    db_path = tmp_path / "panel_real.sqlite"
    db = Database(db_path)
    file_id = db.insert_file(Candidate("/data/source/auth_service.py", FileKind.CODE, 1024, 1.0, 10, 1))
    db.insert_segment(
        file_id=file_id,
        kind=SegmentKind.LINES,
        modality=Modality.TEXT,
        loc=42.0,
        loc_end=45.0,
        excerpt="def authenticate_user(): return True",
        vec=None,
        name="auth_service.py",
        body="def authenticate_user(): return True",
        original="",
    )

    search_service = SearchService(db=db, code_db=db, ollama=None)
    panel = SearchPanel(search_service, AppConfig())
    panel.show()

    panel.search_input.setText("authenticate_user")
    assert panel.is_expanded
    assert panel.result_list.count() > 0

    hit_item = panel.result_list.item(1)
    hit: SearchHit = hit_item.data(Qt.ItemDataRole.UserRole)
    assert isinstance(hit, SearchHit)
    assert hit.path == "/data/source/auth_service.py"
    assert hit.loc == 42.0
    assert "authenticate_user" in hit.excerpt.replace("«", "").replace("»", "")
    assert hit.score > 0.0
    assert hit.keyword_rank > 0

    panel.search_input.setText("code: authenticate_user")
    assert panel.is_expanded
    assert panel.result_list.count() > 0

    panel.shutdown()
    db.close()


def test_panel_async_worker_execution_on_debounce_timer(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify semantic debounce timer triggers async SearchWorker without blocking keystroke thread."""
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.search_input.setText("async_query")

    assert panel._semantic_timer.isActive()
    assert mock_search_service.search.call_count == 0

    panel._semantic_timer.timeout.emit()
    assert panel._worker is not None
    panel._worker.wait()
    qapp.processEvents()

    mock_search_service.search.assert_called_with("async_query", limit=50, is_code=False, prefix_last=True)
    assert len(panel._current_result_set.ranked) == 1
    assert panel.is_expanded
    panel.shutdown()


def test_panel_async_worker_discards_stale_query(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify background worker results for an outdated query are ignored."""
    del qapp
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.search_input.setText("fresh_query")

    stale_hit = SearchHit(path="/tmp/stale.pdf", kind=FileKind.PDF, score=1.0)
    stale_result_set = ResultSet(ranked=[stale_hit], weaker=[])

    panel._on_semantic_results_ready("outdated_query", stale_result_set)
    assert panel._current_result_set.ranked != [stale_hit]
    panel.shutdown()


def test_panel_clearing_text_stops_active_worker(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify clearing search input stops both debounce timer and active background worker."""
    del qapp
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.search_input.setText("hello")
    panel._semantic_timer.timeout.emit()

    assert panel._worker is not None
    worker = panel._worker

    panel.search_input.setText("")
    assert worker.is_stopped
    assert not panel._semantic_timer.isActive()
    assert panel.result_list.count() == 0
    panel.shutdown()


def test_panel_clean_close_and_shutdown(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify closing SearchPanel stops workers and shuts down preview pane cleanly."""
    del qapp
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.search_input.setText("hello")
    panel._semantic_timer.timeout.emit()

    assert panel._worker is not None
    worker = panel._worker
    with patch.object(panel.preview_pane, "shutdown", wraps=panel.preview_pane.shutdown) as mock_preview_shutdown:
        panel.close()
        assert worker.is_stopped
        mock_preview_shutdown.assert_called_once()


def test_panel_double_click_executes_hit_action(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify double-clicking a result executes hit action and hides panel."""
    del qapp
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.show()
    panel.search_input.setText("sample")

    hit_item = panel.result_list.item(1)
    with patch("semsearch.gui.panel.execute_hit_action") as mock_exec, patch.object(panel, "hide_panel") as mock_hide:
        panel._on_item_double_clicked(hit_item)
        mock_exec.assert_called_once()
        mock_hide.assert_called_once()
    panel.shutdown()


def test_panel_slow_worker_lifecycle_no_crash(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify stopping a slow SearchWorker retains it in _active_workers until finished without QThread crash."""
    started = threading.Event()
    unblock = threading.Event()

    def slow_search(query: str, *, limit: int = 50, is_code: bool = False, prefix_last: bool = False) -> ResultSet:
        del limit, is_code, prefix_last
        if query == "slow_query":
            started.set()
            unblock.wait(timeout=2.0)
        return ResultSet(ranked=[], weaker=[])

    mock_search_service.search.side_effect = slow_search
    panel = SearchPanel(mock_search_service, AppConfig())

    panel.search_input.setText("slow_query")
    panel._semantic_timer.timeout.emit()
    assert started.wait(timeout=1.0)
    assert len(panel._active_workers) == 1
    slow_worker = next(iter(panel._active_workers))

    panel.search_input.setText("next_query")
    panel._semantic_timer.timeout.emit()
    gc.collect()

    assert slow_worker.is_stopped
    assert slow_worker in panel._active_workers

    unblock.set()
    for worker in list(panel._active_workers):
        worker.wait(1000)
    qapp.processEvents()

    assert len(panel._active_workers) == 0
    panel.shutdown()


def test_panel_hide_releases_memory_and_ui_state(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify hide_panel clears input, results, preview, collapses geometry, and calls shrink_memory."""
    del qapp
    mock_search_service.code_db = create_autospec(Database, instance=True)
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.show()
    panel.search_input.setText("sample")
    assert bool(panel.is_expanded)
    assert panel.result_list.count() > 0

    with patch.object(panel.preview_pane, "clear", wraps=panel.preview_pane.clear) as mock_preview_clear:
        panel.hide_panel()
        mock_preview_clear.assert_called_once()

    assert panel.search_input.text() == ""
    assert panel._last_query == ""
    assert panel._current_result_set.ranked == []
    assert panel._current_result_set.weaker == []
    assert panel.result_list.count() == 0
    assert not bool(panel.is_expanded)
    assert panel.height() == ThemeLayout.PANEL_HEIGHT_COLLAPSED
    mock_search_service.db.shrink_memory.assert_called_once()
    mock_search_service.code_db.shrink_memory.assert_called_once()
    panel.shutdown()


def test_panel_dismisses_on_window_deactivate(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify SearchPanel automatically hides and releases resources on WindowDeactivate."""
    del qapp
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.show_panel()
    panel.search_input.setText("sample")
    assert panel.isVisible()
    assert bool(panel.is_expanded)

    QApplication.sendEvent(panel, QEvent(QEvent.Type.WindowDeactivate))

    assert not panel.isVisible()
    assert panel.search_input.text() == ""
    assert not bool(panel.is_expanded)
    mock_search_service.db.shrink_memory.assert_called_once()
    panel.shutdown()


def test_panel_stays_visible_on_internal_child_focus(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify moving focus between child widgets inside SearchPanel keeps the panel visible."""
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.show_panel()
    panel.search_input.setText("sample")
    assert panel.isVisible()

    panel.result_list.setFocus()
    qapp.processEvents()
    assert panel.isVisible()
    assert panel.is_expanded

    panel.preview_pane.setFocus()
    qapp.processEvents()
    assert panel.isVisible()
    assert panel.is_expanded
    panel.shutdown()


def test_panel_dismisses_on_preview_action_buttons(
    mock_search_service: MagicMock, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify clicking Open or Show in Files in preview metadata.py passes AppConfig and dismisses panel."""
    del qapp
    executed: list[tuple[SearchHit, AppConfig | None]] = []
    revealed: list[str] = []

    def fake_execute(hit: SearchHit, cfg: AppConfig | None) -> bool:
        executed.append((hit, cfg))
        return True

    monkeypatch.setattr("semsearch.gui.preview.metadata.execute_hit_action", fake_execute)
    monkeypatch.setattr("semsearch.gui.preview.metadata.reveal_in_file_manager", revealed.append)

    cfg = AppConfig(code_editor="zed")
    panel = SearchPanel(mock_search_service, cfg)
    panel.show_panel()
    panel.search_input.setText("sample")
    assert panel.isVisible()

    panel.preview_pane.code_view.meta_card.open_btn.click()
    assert len(executed) == 1
    assert executed[0][0].path == "/tmp/sample.py"
    assert executed[0][1] is cfg
    assert not panel.isVisible()

    panel.show_panel()
    panel.search_input.setText("sample")
    assert panel.isVisible()

    panel.preview_pane.code_view.meta_card.reveal_btn.click()
    assert revealed == ["/tmp/sample.py"]
    assert not panel.isVisible()
    panel.shutdown()


def test_panel_skips_obsolete_intermediate_workers_during_slow_search(
    mock_search_service: MagicMock, qapp: QApplication
) -> None:
    """Verify intermediate debounced workers stopped while waiting on _search_lock skip search()."""
    first_started = threading.Event()
    unblock_first = threading.Event()
    executed_queries: list[str] = []

    def slow_search(query: str, *, limit: int = 50, is_code: bool = False, prefix_last: bool = False) -> ResultSet:
        del limit, is_code, prefix_last
        executed_queries.append(query)
        if query == "query_1":
            first_started.set()
            unblock_first.wait(timeout=2.0)
        return ResultSet(ranked=[], weaker=[])

    mock_search_service.search.side_effect = slow_search
    panel = SearchPanel(mock_search_service, AppConfig())

    panel.search_input.setText("query_1")
    panel._semantic_timer.timeout.emit()
    assert first_started.wait(timeout=1.0)

    panel.search_input.setText("query_2")
    panel._semantic_timer.timeout.emit()

    panel.search_input.setText("query_3")
    panel._semantic_timer.timeout.emit()

    unblock_first.set()
    for worker in list(panel._active_workers):
        worker.wait(1000)
    qapp.processEvents()

    assert executed_queries == ["query_1", "query_3"]
    panel.shutdown()


def test_panel_show_triggers_background_model_preload(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify show_panel() launches a daemon thread to preload the configured embedding model."""
    del qapp
    panel = SearchPanel(mock_search_service, AppConfig())

    panel.show_panel()
    assert panel._preload_thread is not None
    assert panel._preload_thread.daemon is True
    panel._preload_thread.join(timeout=1.0)

    mock_search_service.ollama.preload.assert_called_once_with("embeddinggemma-2")
    panel.shutdown()


def test_panel_show_deduplicates_active_preload_thread(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify calling show_panel() while a preload thread is alive does not spawn a second thread."""
    del qapp
    preload_started = threading.Event()
    unblock_preload = threading.Event()

    def slow_preload(_model: str) -> None:
        preload_started.set()
        unblock_preload.wait(timeout=2.0)

    mock_search_service.ollama.preload.side_effect = slow_preload
    panel = SearchPanel(mock_search_service, AppConfig())

    panel.show_panel()
    assert preload_started.wait(timeout=1.0)
    first_thread = panel._preload_thread
    assert first_thread is not None
    assert first_thread.is_alive()

    panel.show_panel()
    assert panel._preload_thread is first_thread
    assert mock_search_service.ollama.preload.call_count == 1

    unblock_preload.set()
    first_thread.join(timeout=1.0)
    panel.shutdown()


def test_panel_show_preload_ignores_ollama_error(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify OllamaError during background preload is ignored without crashing."""
    del qapp
    mock_search_service.ollama.preload.side_effect = OllamaConnectionError("Offline")
    panel = SearchPanel(mock_search_service, AppConfig())

    panel.show_panel()
    assert panel._preload_thread is not None
    panel._preload_thread.join(timeout=1.0)
    assert not panel._preload_thread.is_alive()
    mock_search_service.ollama.preload.assert_called_once_with("embeddinggemma-2")
    panel.shutdown()


def test_panel_hide_does_not_unload_model(mock_search_service: MagicMock, qapp: QApplication) -> None:
    """Verify hide_panel() does not send an unload request to Ollama."""
    del qapp
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.show_panel()
    assert panel._preload_thread is not None
    panel._preload_thread.join(timeout=1.0)

    mock_search_service.ollama.reset_mock()
    panel.hide_panel()

    assert mock_search_service.ollama.method_calls == []
    panel.shutdown()
