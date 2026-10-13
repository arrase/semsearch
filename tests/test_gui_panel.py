"""Unit and widget tests for quick search panel."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from semsearch.config import AppConfig
from semsearch.core.database import Database
from semsearch.core.types import (
    Candidate,
    FileKind,
    Modality,
    SearchHit,
    SegmentKind,
)
from semsearch.gui.panel import SearchPanel
from semsearch.gui.theme import ThemeLayout
from semsearch.services.searcher import ResultSet


@pytest.fixture
def mock_search_service():
    service = MagicMock()
    service.db.path = "/tmp/test.db"
    # Mock lexical FTS search
    service.db.fts_search.return_value = [
        {"file_id": 1, "rank": 1.0, "snippet": "Sample code snippet", "segment_id": 10, "loc": 5.0, "excerpt": ""}
    ]
    service.code_db = None
    mock_file = MagicMock()
    mock_file.path = "/tmp/sample.py"
    mock_file.kind = FileKind.CODE
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
    panel._semantic_timer.timeout.emit()
    assert panel._worker is not None
    panel._worker.wait()
    qapp.processEvents()
    mock_search_service.search.assert_called_with("hello", limit=50, is_code=False)


def test_weaker_matches_folding_toggle(mock_search_service, qapp: QApplication):
    """Verify folding toggle row expands weaker matches."""
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.search_input.setText("doc")
    panel._semantic_timer.timeout.emit()
    assert panel._worker is not None
    panel._worker.wait()
    qapp.processEvents()

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
    panel._semantic_timer.timeout.emit()
    assert panel._worker is not None
    panel._worker.wait()
    qapp.processEvents()

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


def test_panel_instant_lexical_with_real_database(qapp: QApplication, tmp_path: Path):
    """Verify instant lexical search works with real Database FTS output without artificial mock keys."""
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

    search_service = MagicMock()
    search_service.db = db
    search_service.code_db = None

    panel = SearchPanel(search_service, AppConfig())
    panel.show()

    # Trigger instant lexical search by typing
    panel.search_input.setText("authenticate_user")

    assert panel.is_expanded
    assert panel.result_list.count() > 0

    # The first item is the section header; second item is the hit
    hit_item = panel.result_list.item(1)
    hit: SearchHit = hit_item.data(Qt.ItemDataRole.UserRole)
    assert isinstance(hit, SearchHit)
    assert hit.path == "/data/source/auth_service.py"
    assert hit.loc == 42.0
    assert "authenticate_user" in hit.excerpt
    assert hit.score > 0.0
    assert hit.keyword_rank > 0

    # Also test code: prefix search
    search_service.code_db = db
    panel.search_input.setText("code: authenticate_user")
    assert panel.is_expanded
    assert panel.result_list.count() > 0

    db.close()


def test_panel_instant_lexical_negative_bm25_contract(qapp: QApplication):
    """Verify negative BM25 rank inversion and hit extraction matching Database.fts_search contract."""
    service = MagicMock()
    service.code_db = None
    # Dict matching Database.fts_search contract with negative BM25 rank
    service.db.fts_search.return_value = [
        {"file_id": 1, "rank": -4.2, "snippet": "«handler» function", "segment_id": 101, "loc": 0.0, "excerpt": ""}
    ]
    mock_file = MagicMock()
    mock_file.path = "/tmp/handler.py"
    mock_file.kind = FileKind.CODE
    service.db.find_by_id.return_value = mock_file

    panel = SearchPanel(service, AppConfig())
    panel.show()
    panel.search_input.setText("handler")

    assert panel.is_expanded
    hit_item = panel.result_list.item(1)
    hit: SearchHit = hit_item.data(Qt.ItemDataRole.UserRole)
    assert hit.score == pytest.approx(4.2)
    assert hit.keyword_rank == 4
    assert hit.loc == 0.0
    assert hit.excerpt == "«handler» function"


def test_panel_instant_lexical_none_values_and_score_sorting(qapp: QApplication):
    """Verify panel handles 0.0 rank, sorting out-of-order FTS hits by score descending."""
    service = MagicMock()
    service.code_db = None
    service.db.fts_search.return_value = [
        # Rank -4.2 -> score 4.2
        {"file_id": 1, "rank": -4.2, "snippet": "mid match", "segment_id": 1, "loc": 0.0, "excerpt": ""},
        # Rank -8.0 -> score 8.0
        {"file_id": 2, "rank": -8.0, "snippet": "high match", "segment_id": 2, "loc": 10.0, "excerpt": ""},
        # Rank 0.0 -> score 0.0
        {"file_id": 3, "rank": 0.0, "snippet": "zero rank", "segment_id": 3, "loc": 0.0, "excerpt": ""},
    ]

    def mock_find(fid: int):
        rec = MagicMock()
        rec.path = f"/tmp/file_{fid}.py"
        rec.kind = FileKind.CODE
        return rec

    service.db.find_by_id.side_effect = mock_find

    panel = SearchPanel(service, AppConfig())
    panel.show()
    panel.search_input.setText("match")

    assert panel.is_expanded
    assert panel._current_result_set is not None
    ranked = panel._current_result_set.ranked
    weaker = panel._current_result_set.weaker
    assert len(ranked) == 2
    assert len(weaker) == 1

    assert ranked[0].score == pytest.approx(8.0)
    assert ranked[0].loc == 10.0
    assert ranked[0].keyword_rank == 8

    assert ranked[1].score == pytest.approx(4.2)
    assert ranked[1].loc == 0.0
    assert ranked[1].keyword_rank == 4

    assert weaker[0].score == pytest.approx(0.0)
    assert weaker[0].loc == 0.0
    assert weaker[0].keyword_rank == 0


def test_panel_async_worker_execution_on_debounce_timer(mock_search_service, qapp: QApplication):
    """Verify semantic debounce timer triggers async SearchWorker without blocking keystroke thread."""
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.search_input.setText("async_query")

    # Lexical instant search ran, but semantic search has not run synchronously
    assert panel._semantic_timer.isActive()
    assert mock_search_service.search.call_count == 0

    # Trigger debounce timeout to launch worker
    panel._semantic_timer.timeout.emit()
    assert panel._worker is not None
    panel._worker.wait()
    qapp.processEvents()

    mock_search_service.search.assert_called_with("async_query", limit=50, is_code=False)
    assert len(panel._current_result_set.ranked) == 1
    assert panel.is_expanded


def test_panel_async_worker_discards_stale_query(mock_search_service, qapp: QApplication):
    """Verify background worker results for an outdated query are ignored."""
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.search_input.setText("fresh_query")

    stale_hit = SearchHit(path="/tmp/stale.pdf", kind=FileKind.PDF, score=1.0)
    stale_result_set = ResultSet(ranked=[stale_hit], weaker=[])

    panel._on_semantic_results_ready("outdated_query", stale_result_set)
    # The current result set should not contain the stale hit
    assert panel._current_result_set.ranked != [stale_hit]


def test_panel_clearing_text_stops_active_worker(mock_search_service, qapp: QApplication):
    """Verify clearing search input stops both debounce timer and active background worker."""
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.search_input.setText("hello")
    panel._semantic_timer.timeout.emit()

    assert panel._worker is not None
    worker = panel._worker

    # Clearing search input must stop running worker
    panel.search_input.setText("")
    assert worker.is_stopped
    assert not panel._semantic_timer.isActive()
    assert panel.result_list.count() == 0


def test_panel_clean_close_with_active_worker(mock_search_service, qapp: QApplication):
    """Verify closing SearchPanel while worker is running cleans up without crashing."""
    panel = SearchPanel(mock_search_service, AppConfig())
    panel.search_input.setText("hello")
    panel._semantic_timer.timeout.emit()

    assert panel._worker is not None
    worker = panel._worker
    panel.close()
    assert worker.is_stopped


def test_panel_instant_lexical_deduplication_and_double_click(qapp: QApplication):
    """Verify instant lexical search deduplicates multiple segments per file_id and double-click opens hit."""
    service = MagicMock()
    service.code_db = None
    service.db.fts_search.return_value = [
        {"file_id": 1, "rank": -3.0, "snippet": "lower segment", "segment_id": 10, "loc": 2.0, "excerpt": ""},
        {"file_id": 1, "rank": -9.5, "snippet": "best segment", "segment_id": 11, "loc": 5.0, "excerpt": ""},
    ]
    mock_file = MagicMock()
    mock_file.path = "/tmp/report.pdf"
    mock_file.kind = FileKind.PDF
    mock_file.size = 4096
    mock_file.mtime = 1700000000.0
    service.db.find_by_id.return_value = mock_file

    panel = SearchPanel(service, AppConfig())
    panel.show()
    panel.search_input.setText("report")

    assert len(panel._current_result_set.ranked) == 1
    hit = panel._current_result_set.ranked[0]
    assert hit.score == pytest.approx(9.5)
    assert hit.loc == 5.0
    assert hit.size == 4096

    hit_item = panel.result_list.item(1)
    with patch("semsearch.gui.panel.execute_hit_action") as mock_exec, patch.object(panel, "hide_panel") as mock_hide:
        panel._on_item_double_clicked(hit_item)
        mock_exec.assert_called_once()
        mock_hide.assert_called_once()



