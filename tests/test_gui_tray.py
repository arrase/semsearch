"""Unit tests for SystemTrayManager, HiDPI tray icon, and idle-zero-polling behavior."""

import threading
from typing import cast
from unittest.mock import MagicMock, create_autospec, patch

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from semsearch.core.types import EngineActivity, EngineStatus
from semsearch.gui.tray import SystemTrayManager, render_tray_icon
from semsearch.i18n import load_catalog, set_language
from semsearch.services.indexer import IndexService


def _make_mock_indexer(status: EngineStatus) -> MagicMock:
    indexer = cast(MagicMock, create_autospec(IndexService, instance=True))
    indexer.get_status.return_value = status
    return indexer


def test_render_tray_icon_hidpi_and_states(qapp: QApplication) -> None:
    """Verify HiDPI 128x128 @2x icon generation across idle, indexing, and paused states."""
    del qapp
    icon_idle = render_tray_icon(0.0, is_indexing=False, is_paused=False)
    assert not icon_idle.isNull()
    assert icon_idle.availableSizes() == [QSize(128, 128)]
    assert icon_idle.pixmap(64, 64).width() == 64
    hidpi_pm = icon_idle.pixmap(QSize(64, 64), 2.0)
    assert hidpi_pm.width() == 128
    assert hidpi_pm.height() == 128
    assert hidpi_pm.devicePixelRatio() == 2.0

    icon_indexing = render_tray_icon(45.0, is_indexing=True, is_paused=False)
    assert not icon_indexing.isNull()

    icon_paused = render_tray_icon(45.0, is_indexing=False, is_paused=True)
    assert not icon_paused.isNull()


def test_system_tray_manager_menu_actions(qapp: QApplication) -> None:
    """Verify menu action signal wiring."""
    del qapp
    manager = SystemTrayManager()

    panel_spy = MagicMock()
    settings_spy = MagicMock()
    quit_spy = MagicMock()

    manager.open_panel_requested.connect(panel_spy)
    manager.open_settings_requested.connect(settings_spy)
    manager.quit_requested.connect(quit_spy)

    manager.panel_action.trigger()
    panel_spy.assert_called_once()

    manager.settings_action.trigger()
    settings_spy.assert_called_once()

    manager.quit_action.trigger()
    quit_spy.assert_called_once()


def test_system_tray_manager_status_updates_and_waiting_reasons(qapp: QApplication) -> None:
    """Verify tray text, pause toggle, and translated waiting_for reasons."""
    del qapp
    indexing_status = EngineStatus(
        activity=EngineActivity.INDEXING,
        indexed=50,
        total=100,
        paused=False,
    )
    mock_indexer = _make_mock_indexer(indexing_status)
    manager = SystemTrayManager(index_service=mock_indexer)

    manager.update_status(indexing_status)
    assert "Indexing: 50%" in manager.status_action.text()
    assert "Pause Indexing" in manager.pause_action.text()
    assert "Indexing: 50%" in manager.tray_icon.toolTip()

    paused_status = EngineStatus(activity=EngineActivity.PAUSED, indexed=50, total=100, paused=True)
    mock_indexer.get_status.side_effect = [indexing_status, paused_status]
    manager.pause_action.trigger()
    mock_indexer.pause.assert_called_once()
    assert manager.status_action.text() == "Paused"
    assert "Resume Indexing" in manager.pause_action.text()

    # Translated waiting_for reasons in English
    manager.update_status(EngineStatus(activity=EngineActivity.PAUSED, paused=False, waiting_for="Ollama"))
    assert manager.status_action.text() == "Waiting for Ollama"
    assert "Waiting for Ollama" in manager.tray_icon.toolTip()

    manager.update_status(EngineStatus(activity=EngineActivity.PAUSED, paused=True, waiting_for="AC power"))
    assert manager.status_action.text() == "Waiting for AC power"

    manager.update_status(EngineStatus(activity=EngineActivity.PAUSED, paused=True, waiting_for="Scheduled hours"))
    assert manager.status_action.text() == "Waiting for scheduled hours"

    # Translated waiting_for reason in Spanish
    set_language("es")
    es_catalog = load_catalog("es")
    mock_indexer.get_status.side_effect = None
    mock_indexer.get_status.return_value = EngineStatus(
        activity=EngineActivity.PAUSED, paused=False, waiting_for="Ollama"
    )
    manager.retranslate_ui()
    assert manager.status_action.text() == es_catalog["Waiting for Ollama"]
    set_language("en")

    # Idle status
    idle_status = EngineStatus(activity=EngineActivity.IDLE, indexed=120, total=120, paused=False)
    manager.update_status(idle_status)
    assert "Idle - 120 indexed" in manager.status_action.text()
    manager.stop()


def test_system_tray_manager_zero_polling_on_idle_and_work_listener_wakeup(qapp: QApplication) -> None:
    """Verify poll timer stops in IDLE, wakes on IndexService work listener across threads, and stops on shutdown."""
    indexing_status = EngineStatus(activity=EngineActivity.INDEXING, indexed=10, total=50, paused=False)
    mock_indexer = _make_mock_indexer(indexing_status)

    manager = SystemTrayManager(index_service=mock_indexer)
    mock_indexer.add_work_listener.assert_called_once()
    registered_listener = mock_indexer.add_work_listener.call_args.args[0]

    assert manager._poll_timer is not None
    assert manager._poll_timer.isActive()
    assert manager._poll_timer.interval() == 1000

    # Periodic timeout updates status while indexing
    mock_indexer.get_status.return_value = EngineStatus(
        activity=EngineActivity.INDEXING, indexed=25, total=50, paused=False
    )
    manager._poll_timer.timeout.emit()
    assert "Indexing: 50%" in manager.status_action.text()
    assert manager._poll_timer.isActive()

    # Transitioning to IDLE stops the polling timer completely
    mock_indexer.get_status.return_value = EngineStatus(
        activity=EngineActivity.IDLE, indexed=50, total=50, paused=False
    )
    manager._poll_timer.timeout.emit()
    assert not manager._poll_timer.isActive()
    assert "Idle - 50 indexed" in manager.status_action.text()

    # Cross-thread work arrival restarts polling and updates tray state
    mock_indexer.get_status.return_value = EngineStatus(
        activity=EngineActivity.INDEXING, indexed=51, total=60, paused=False
    )
    worker_thread = threading.Thread(target=registered_listener)
    worker_thread.start()
    worker_thread.join(timeout=2.0)
    qapp.processEvents()

    assert manager._poll_timer.isActive()
    assert "Indexing: 85%" in manager.status_action.text()

    # Explicit stop halts polling cleanly
    manager.stop()
    assert not manager._poll_timer.isActive()


def test_system_tray_manager_no_indexer(qapp: QApplication) -> None:
    """Verify SystemTrayManager without index_service does not initialize poll timer."""
    del qapp
    manager = SystemTrayManager(index_service=None)
    assert manager._poll_timer is None
    manager._poll_status()
    manager.stop()


def test_system_tray_manager_skips_unchanged_status_updates(qapp: QApplication) -> None:
    """Verify repeated update_status calls with unchanged state skip icon rendering and D-Bus updates."""
    del qapp
    idle_status = EngineStatus(activity=EngineActivity.IDLE, indexed=100, total=100, paused=False)
    mock_indexer = _make_mock_indexer(idle_status)

    manager = SystemTrayManager(index_service=mock_indexer)

    with (
        patch("semsearch.gui.tray.render_tray_icon", wraps=render_tray_icon) as mock_render,
        patch.object(manager.tray_icon, "setIcon") as mock_set_icon,
        patch.object(manager.tray_icon, "setToolTip") as mock_set_tooltip,
        patch.object(manager.status_action, "setText") as mock_set_status_text,
    ):
        manager.update_status(idle_status)
        assert mock_render.call_count == 1
        assert mock_set_icon.call_count == 1
        assert mock_set_tooltip.call_count == 1
        assert mock_set_status_text.call_count == 1
        assert manager._poll_timer is not None
        assert not manager._poll_timer.isActive()

        # Repeated update with identical state skips rendering
        manager.update_status(idle_status)
        assert mock_render.call_count == 1
        assert mock_set_icon.call_count == 1
        assert mock_set_tooltip.call_count == 1
        assert mock_set_status_text.call_count == 1

        # retranslate_ui invalidates _last_rendered_state and forces a refresh
        manager.retranslate_ui()
        assert mock_render.call_count == 2
        assert mock_set_icon.call_count == 2
        assert mock_set_tooltip.call_count == 2
        assert mock_set_status_text.call_count == 2
    manager.stop()
