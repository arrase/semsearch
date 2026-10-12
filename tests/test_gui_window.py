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

    with patch("semsearch.gui.window.open_code_editor") as mock_open:
        item = code_list.item(0)
        code_list._on_item_double_clicked(item)
        mock_open.assert_called_once_with("/tmp/test.py", line=15)


def test_result_card_drag_mime_data(qapp: QApplication):
    """Verify ResultCard initiates drag with local file URI."""
    hit = SearchHit(path="/tmp/doc.pdf", kind=FileKind.PDF, score=1.0)
    card = ResultCard(hit)

    with patch("PySide6.QtGui.QDrag.exec_") as mock_exec:
        card._start_drag()
        mock_exec.assert_called_once_with(Qt.DropAction.CopyAction)
