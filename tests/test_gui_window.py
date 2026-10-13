"""Unit and widget tests for full desktop search window."""

from unittest.mock import MagicMock, patch
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from semsearch.config import AppConfig
from semsearch.core.types import FileKind, SearchHit
from semsearch.gui.theme import ThemeLayout
from semsearch.gui.window import CodeListView, ResultCard, SearchWindow
from semsearch.gui.worker import SearchWorker
from semsearch.services.searcher import ResultSet


@pytest.fixture
def mock_search_service():
    service = MagicMock()
    hit_pdf = SearchHit(path="/tmp/doc.pdf", kind=FileKind.PDF, score=1.0, excerpt="A PDF document")
    hit_img = SearchHit(path="/tmp/photo.jpg", kind=FileKind.IMAGE, score=0.9, excerpt="A JPEG photo")
    hit_code = SearchHit(path="/tmp/test.py", kind=FileKind.CODE, score=0.8, excerpt="def foo(): pass", focus=10.0)
    service.search.return_value = ResultSet(ranked=[hit_pdf, hit_img, hit_code], weaker=[])
    return service


def test_window_geometry_and_components(mock_search_service, qapp: QApplication):
    """Verify window dimensions, constraints, and initial components."""
    win = SearchWindow(mock_search_service, AppConfig())
    assert win.width() == ThemeLayout.WINDOW_WIDTH
    assert win.height() == ThemeLayout.WINDOW_HEIGHT
    assert win.minimumWidth() == ThemeLayout.WINDOW_MIN_WIDTH
    assert win.minimumHeight() == ThemeLayout.WINDOW_MIN_HEIGHT
    assert win.search_input is not None
    assert win.preview_pane is not None
    assert win.splitter is not None


def test_kind_chips_filtering(mock_search_service, qapp: QApplication):
    """Verify kind chips update active filter and switch content stack."""
    win = SearchWindow(mock_search_service, AppConfig())
    win.perform_search("query")

    # Initial state All
    assert win._active_kind is None
    assert win.content_stack.currentIndex() == 0  # Grid view
    assert len(win.card_grid._cards) == 3

    # Switch to Code (Ctrl+6)
    win.set_kind_filter(FileKind.CODE)
    assert win._active_kind == FileKind.CODE
    assert win.content_stack.currentIndex() == 1  # Code list view
    assert win.code_list.count() == 1

    # Switch to PDF (Ctrl+2)
    win.set_kind_filter(FileKind.PDF)
    assert win._active_kind == FileKind.PDF
    assert win.content_stack.currentIndex() == 0  # Grid view
    assert len(win.card_grid._cards) == 1

    # Switch back to All
    win.set_kind_filter(None)
    assert win._active_kind is None
    assert len(win.card_grid._cards) == 3


def test_card_grid_selection_sync(mock_search_service, qapp: QApplication):
    """Verify selecting a card updates preview pane."""
    win = SearchWindow(mock_search_service, AppConfig())
    win.perform_search("query")

    card = win.card_grid._cards[1]
    with patch.object(win.preview_pane, "load_result") as mock_load:
        QTest.mouseClick(card, Qt.MouseButton.LeftButton)
        mock_load.assert_called_once()


def test_code_list_jump_to_definition(qapp: QApplication):
    """Verify double clicking a code item opens code editor at definition line."""
    code_list = CodeListView()
    hit = SearchHit(path="/tmp/test.py", kind=FileKind.CODE, score=1.0, excerpt="def foo():", focus=15.0)
    code_list.set_results([hit])

    with patch("semsearch.gui.window.open_code_at") as mock_open:
        item = code_list.item(0)
        code_list._on_item_double_clicked(item)
        mock_open.assert_called_once_with("/tmp/test.py", line=15, editor_preference="auto")


def test_result_card_drag_mime_data(qapp: QApplication):
    """Verify ResultCard initiates drag with local file URI."""
    hit = SearchHit(path="/tmp/doc.pdf", kind=FileKind.PDF, score=1.0)
    card = ResultCard(hit)

    with patch("PySide6.QtGui.QDrag.exec_") as mock_exec:
        card._start_drag()
        mock_exec.assert_called_once_with(Qt.DropAction.CopyAction)


def test_window_debounce_timer_activation_and_clearing(mock_search_service, qapp: QApplication):
    """Verify entering search text starts debounce timer and clearing text resets."""
    win = SearchWindow(mock_search_service, AppConfig())
    win.search_input.setText("photos")

    # Debounce timer active, search service NOT called synchronously on typing
    assert win._debounce_timer.isActive()
    assert mock_search_service.search.call_count == 0

    # Clearing text immediately stops timer and resets UI
    win.search_input.setText("")
    assert not win._debounce_timer.isActive()
    assert win._current_hits == []


def test_window_async_search_worker_execution(mock_search_service, qapp: QApplication):
    """Verify firing debounce timer spawns background worker and delivers results."""
    win = SearchWindow(mock_search_service, AppConfig())
    win.search_input.setText("hello")
    assert win._debounce_timer.isActive()

    # Trigger debounce timeout
    win._debounce_timer.timeout.emit()
    assert win._worker is not None
    win._worker.wait()
    qapp.processEvents()

    mock_search_service.search.assert_called_with("hello", limit=100, is_code=False)
    assert len(win._current_hits) == 3
    assert len(win.card_grid._cards) == 3


def test_window_async_worker_discards_stale_results(mock_search_service, qapp: QApplication):
    """Verify results from a stale query are discarded if input changed."""
    win = SearchWindow(mock_search_service, AppConfig())
    win.search_input.setText("new_query")

    old_results = ResultSet(
        ranked=[SearchHit(path="/tmp/stale.pdf", kind=FileKind.PDF, score=0.5)],
        weaker=[],
    )
    win._on_search_results_ready("old_query", old_results)

    # Stale results should be ignored
    assert win._current_hits == []


def test_search_worker_thread_execution():
    """Verify SearchWorker executes search and emits results_ready."""
    service = MagicMock()
    expected = ResultSet(ranked=[SearchHit(path="/tmp/a.txt", kind=FileKind.DOC, score=1.0)], weaker=[])
    service.search.return_value = expected

    worker = SearchWorker(service, "query", limit=10)
    received = []
    worker.results_ready.connect(lambda q, r: received.append((q, r)))
    worker.run()
    service.search.assert_called_with("query", limit=10, is_code=False)
    assert received == [("query", expected)]


def test_search_worker_stop_interruption():
    """Verify SearchWorker stop marks interruption and suppresses signal emission."""
    service = MagicMock()
    service.search.return_value = ResultSet()

    worker = SearchWorker(service, "query", limit=10)
    signal_received = []
    worker.results_ready.connect(lambda q, r: signal_received.append((q, r)))

    worker.stop()
    assert worker.is_stopped

    worker.run()
    assert len(signal_received) == 0


def test_window_clearing_text_stops_active_worker(mock_search_service, qapp: QApplication):
    """Verify clearing search input stops both debounce timer and active background worker."""
    win = SearchWindow(mock_search_service, AppConfig())
    win.search_input.setText("hello")
    win._debounce_timer.timeout.emit()

    assert win._worker is not None
    worker = win._worker

    # Clearing input text must stop the running worker
    win.search_input.setText("")
    assert worker.is_stopped
    assert not win._debounce_timer.isActive()
    assert win._current_hits == []


def test_window_clean_close_with_active_worker(mock_search_service, qapp: QApplication):
    """Verify closing SearchWindow while worker is running cleans up without crashing."""
    win = SearchWindow(mock_search_service, AppConfig())
    win.search_input.setText("hello")
    win._debounce_timer.timeout.emit()

    assert win._worker is not None
    worker = win._worker
    win.close()
    assert worker.is_stopped


def test_window_code_filter_routing(mock_search_service, qapp: QApplication):
    """Verify Code chip activation and code: prefix route queries to code search."""
    win = SearchWindow(mock_search_service, AppConfig())

    # Case 1: Code chip active routes perform_search with is_code=True
    win.set_kind_filter(FileKind.CODE)
    win.perform_search("def compute")
    mock_search_service.search.assert_called_with("def compute", limit=100, is_code=True)

    # Case 2: Entering code: prefix activates Code chip and triggers worker with is_code=True
    win.set_kind_filter(None)
    win.search_input.setText("code: calculate")
    assert win._active_kind == FileKind.CODE

    win._debounce_timer.timeout.emit()
    assert win._worker is not None
    win._worker.wait()
    qapp.processEvents()
    mock_search_service.search.assert_called_with("code: calculate", limit=100, is_code=True)

    # Case 3: Uppercase CODE: prefix also activates Code chip
    win.set_kind_filter(None)
    win.search_input.setText("CODE: parse_tokens")
    assert win._active_kind == FileKind.CODE

    # Case 4: Switching away from Code chip strips code: prefix so general search searches the term
    win.search_input.setText("code: authentication")
    win.set_kind_filter(FileKind.DOC)
    assert win.search_input.text() == "authentication"
    assert win._active_kind == FileKind.DOC


def test_window_weaker_matches_folding_and_selection(qapp: QApplication):
    """Verify CardGridView folds weaker matches and highlights the selected ResultCard."""
    service = MagicMock()
    hit_strong = SearchHit(path="/tmp/strong.pdf", kind=FileKind.PDF, score=1.0)
    hit_weak = SearchHit(path="/tmp/weak.pdf", kind=FileKind.PDF, score=0.2)
    service.search.return_value = ResultSet(ranked=[hit_strong], weaker=[hit_weak])

    win = SearchWindow(service, AppConfig())
    win.show()
    win.perform_search("report")

    assert len(win.card_grid._cards) == 1
    assert len(win.card_grid._weaker_cards) == 1
    assert win.card_grid._cards[0]._is_selected
    assert not win.card_grid.weaker_grid_widget.isVisible()

    win.card_grid.weaker_btn.click()
    assert win.card_grid.weaker_grid_widget.isVisible()


def test_code_list_single_line_formatting(qapp: QApplication):
    """Verify CodeListView strips newlines and FTS markers and shows repo_rel_path/def_name."""
    code_list = CodeListView()
    hit = SearchHit(
        path="/repo/src/engine.py",
        kind=FileKind.CODE,
        score=1.0,
        excerpt="def «compute»(\n    x: int,\n) -> int:",
        focus=42.0,
        info={"repo_rel_path": "src/engine.py", "def_name": "compute"},
    )
    code_list.set_results([hit])
    item_text = code_list.item(0).text()
    assert "\n" not in item_text
    assert "«" not in item_text
    assert "src/engine.py:L42" in item_text
    assert "[compute]" in item_text

