"""Unit and widget tests for quick search panel."""

from unittest.mock import MagicMock, patch
import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from semsearch.config import AppConfig
from semsearch.core.types import FileKind, SearchHit
from semsearch.gui.panel import SearchPanel
from semsearch.gui.theme import ThemeLayout
from semsearch.services.searcher import ResultSet


@pytest.fixture
def mock_search_service():
    service = MagicMock()
    service.db.path = "/tmp/test.db"
    service.db.db_path = "/tmp/test.db"
    # Mock lexical FTS search
    service.db.fts_search.return_value = [
        {"file_id": 1, "rank": 1.0, "snippet": "Sample code snippet", "segment_id": 10, "loc": 5.0}
    ]
    service.code_db = None
    mock_file = MagicMock()
    mock_file.path = "/tmp/sample.py"
    mock_file.kind = FileKind.CODE
    service.db.find_file.return_value = mock_file
    service.db.find_by_id.return_value = mock_file

    # Mock hybrid search
    hit1 = SearchHit(path="/tmp/doc.pdf", kind=FileKind.PDF, score=1.0)
    hit_weaker = SearchHit(path="/tmp/old.txt", kind=FileKind.DOC, score=0.2)
    service.search.return_value = ResultSet(ranked=[hit1], weaker=[hit_weaker])
    return service


def test_panel_geometry_and_flags(mock_search_service, qapp: QApplication):
    """Verify window flags and initial collapsed geometry."""
    panel = SearchPanel(mock_search_service, AppConfig())
    flags = panel.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert panel.width() == ThemeLayout.PANEL_WIDTH
    assert panel.height() == ThemeLayout.PANEL_HEIGHT_COLLAPSED
    assert not panel.is_expanded


def test_panel_expansion_on_query(mock_search_service, qapp: QApplication):
    """Verify panel expands when results match."""
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.show()
    panel.search_input.setText("sample")

    assert panel.is_expanded
    assert panel.height() == ThemeLayout.PANEL_HEIGHT_EXPANDED
    assert panel.content_widget.isVisible()
    assert panel.result_list.count() > 0

    # Clearing text collapses
    panel.search_input.setText("")
    assert not panel.is_expanded
    assert panel.height() == ThemeLayout.PANEL_HEIGHT_COLLAPSED
    assert not panel.content_widget.isVisible()


def test_panel_semantic_debounce_timer(mock_search_service, qapp: QApplication):
    """Verify semantic debounce timer triggers hybrid search."""
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.search_input.setText("hello")
    assert panel._semantic_timer.isActive()

    # Trigger debounce timeout
    panel._on_semantic_debounce_fired()
    mock_search_service.search.assert_called_with("hello", limit=50)


def test_weaker_matches_folding_toggle(mock_search_service, qapp: QApplication):
    """Verify folding toggle row expands weaker matches."""
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.search_input.setText("doc")
    panel._on_semantic_debounce_fired()

    # Find the weaker matches toggle item
    toggle_row = -1
    for idx in range(panel.result_list.count()):
        item = panel.result_list.item(idx)
        if item.data(Qt.ItemDataRole.UserRole + 1) == "toggle":
            toggle_row = idx
            break

    assert toggle_row != -1
    initial_count = panel.result_list.count()

    # Click toggle item to unfold
    panel._on_item_clicked(panel.result_list.item(toggle_row))
    assert panel.weaker_expanded
    assert panel.result_list.count() > initial_count


def test_keyboard_navigation_and_actions(mock_search_service, qapp: QApplication):
    """Verify arrow navigation, Enter activation, Shift+Enter reveal, Ctrl+C copy, Ctrl+O jump, and Escape."""
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.search_input.setText("doc")
    panel._on_semantic_debounce_fired()

    # Arrow Down
    event_down = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
    panel.eventFilter(panel.search_input, event_down)
    assert panel.result_list.currentRow() >= 0

    # Ensure valid item selected for action shortcuts
    hit_row = -1
    for idx in range(panel.result_list.count()):
        if isinstance(panel.result_list.item(idx).data(Qt.ItemDataRole.UserRole), SearchHit):
            hit_row = idx
            break
    assert hit_row != -1
    panel.result_list.setCurrentRow(hit_row)

    # 1. Enter (execute_hit_action)
    with patch("semsearch.gui.panel.execute_hit_action") as mock_exec, patch.object(panel, "hide_panel") as mock_hide:
        event_enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
        panel.eventFilter(panel.search_input, event_enter)
        mock_exec.assert_called_once()
        mock_hide.assert_called_once()

    # 2. Shift+Enter (reveal_in_file_manager)
    with patch("semsearch.gui.panel.reveal_in_file_manager") as mock_reveal, patch.object(panel, "hide_panel") as mock_hide:
        event_shift_enter = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        panel.eventFilter(panel.search_input, event_shift_enter)
        mock_reveal.assert_called_once()
        mock_hide.assert_called_once()

    # 3. Ctrl+C (copy_file_to_clipboard)
    with patch("semsearch.gui.panel.copy_file_to_clipboard") as mock_copy:
        event_ctrl_c = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        panel.eventFilter(panel.search_input, event_ctrl_c)
        mock_copy.assert_called_once_with("/tmp/doc.pdf")

    # 4. Ctrl+O (open_window_requested)
    with patch.object(panel, "hide_panel") as mock_hide:
        open_window_spy = MagicMock()
        panel.open_window_requested.connect(open_window_spy)
        event_ctrl_o = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier)
        panel.eventFilter(panel.search_input, event_ctrl_o)
        mock_hide.assert_called_once()
        open_window_spy.assert_called_once()

    # Escape with text clears input
    event_esc = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    panel.eventFilter(panel.search_input, event_esc)
    assert panel.search_input.text() == ""
    assert not panel.is_expanded

    # Escape with empty input hides panel
    with patch.object(panel, "hide_panel") as mock_hide:
        panel.eventFilter(panel.search_input, event_esc)
        mock_hide.assert_called_once()
