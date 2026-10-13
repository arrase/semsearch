"""Unit tests for SystemTrayManager and dynamic circular progress ring rendering."""

from unittest.mock import MagicMock
from PySide6.QtWidgets import QApplication
from semsearch.core.types import EngineActivity, EngineStatus
from semsearch.gui.tray import SystemTrayManager, render_tray_icon


def test_render_tray_icon_states(qapp: QApplication):
    """Verify icon generation across idle, indexing, and paused states."""
    icon_idle = render_tray_icon(0.0, is_indexing=False, is_paused=False)
    assert not icon_idle.isNull()
    assert icon_idle.pixmap(64, 64).width() == 64

    icon_indexing = render_tray_icon(45.0, is_indexing=True, is_paused=False)
    assert not icon_indexing.isNull()

    icon_paused = render_tray_icon(45.0, is_indexing=False, is_paused=True)
    assert not icon_paused.isNull()


def test_system_tray_manager_menu_actions(qapp: QApplication):
    """Verify menu action signal wiring."""
    manager = SystemTrayManager()

    panel_spy = MagicMock()
    window_spy = MagicMock()
    settings_spy = MagicMock()
    quit_spy = MagicMock()

    manager.open_panel_requested.connect(panel_spy)
    manager.open_window_requested.connect(window_spy)
    manager.open_settings_requested.connect(settings_spy)
    manager.quit_requested.connect(quit_spy)

    manager.panel_action.trigger()
    panel_spy.assert_called_once()

    manager.window_action.trigger()
    window_spy.assert_called_once()

    manager.settings_action.trigger()
    settings_spy.assert_called_once()

    manager.quit_action.trigger()
    quit_spy.assert_called_once()


def test_system_tray_manager_status_updates(qapp: QApplication):
    """Verify tray icon text and tooltip update according to EngineStatus."""
    mock_indexer = MagicMock()
    mock_indexer.status = EngineStatus(
        activity=EngineActivity.INDEXING,
        indexed=50,
        total=100,
        paused=False,
    )

    manager = SystemTrayManager(index_service=mock_indexer)

    # Initial indexing status
    manager.update_status(mock_indexer.status)
    assert "Indexing: 50%" in manager.status_action.text()
    assert "Pause Indexing" in manager.pause_action.text()
    assert "Indexing: 50%" in manager.tray_icon.toolTip()

    # Toggle pause via menu action
    manager.pause_action.trigger()
    mock_indexer.pause.assert_called_once()

    # Status paused
    mock_indexer.status.paused = True
    mock_indexer.status.activity = EngineActivity.PAUSED
    manager.update_status(mock_indexer.status)
    assert manager.status_action.text() == "Paused"
    assert "Resume Indexing" in manager.pause_action.text()

    # Status idle
    idle_status = EngineStatus(
        activity=EngineActivity.IDLE,
        indexed=120,
        total=120,
        paused=False,
    )
    manager.update_status(idle_status)
    assert "Idle - 120 indexed" in manager.status_action.text()


def test_system_tray_manager_periodic_poll_timer(qapp: QApplication):
    """Verify periodic QTimer is configured and updates status periodically."""
    mock_indexer = MagicMock()
    mock_indexer.status = EngineStatus(
        activity=EngineActivity.INDEXING,
        indexed=10,
        total=50,
        paused=False,
    )

    manager = SystemTrayManager(index_service=mock_indexer)
    assert manager._poll_timer is not None
    assert manager._poll_timer.isActive()
    assert manager._poll_timer.interval() == 1000

    # Simulate timer trigger via timeout signal
    mock_indexer.status = EngineStatus(
        activity=EngineActivity.INDEXING,
        indexed=25,
        total=50,
        paused=False,
    )
    manager._poll_timer.timeout.emit()
    assert "Indexing: 50%" in manager.status_action.text()
    assert "Indexing: 50%" in manager.tray_icon.toolTip()


def test_system_tray_manager_no_indexer(qapp: QApplication):
    """Verify SystemTrayManager without index_service does not initialize poll timer."""
    manager = SystemTrayManager(index_service=None)
    assert manager._poll_timer is None
    manager._poll_status()
