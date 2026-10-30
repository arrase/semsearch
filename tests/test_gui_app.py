"""Unit and integration tests for SemSearchApp shell, IPC server, hotkeys, and CLI dispatcher."""

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import shiboken6
from pynput.keyboard import HotKey
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication

from semsearch.config import AppConfig, ConfigManager
from semsearch.core.database import Database
from semsearch.gui.app import (
    SemSearchApp,
    forward_ipc_command,
    get_ipc_server_name,
)
from semsearch.gui.hotkey import HotkeyListener, normalize_hotkey
from semsearch.main import build_parser


def test_normalize_hotkey() -> None:
    """Verify conversion to pynput format and HotKey.parse compatibility."""
    assert normalize_hotkey("ctrl+shift+space") == "<ctrl>+<shift>+<space>"
    assert normalize_hotkey("<ctrl>+<shift>+<space>") == "<ctrl>+<shift>+<space>"
    assert normalize_hotkey("ctrl+alt+k") == "<ctrl>+<alt>+k"
    assert normalize_hotkey("<ctrl>+<alt>+<k>") == "<ctrl>+<alt>+k"

    for combo in ("ctrl+shift+f", "ctrl+alt+space", "ctrl+1", "ctrl+alt+k"):
        parsed = HotKey.parse(normalize_hotkey(combo))
        assert len(parsed) > 0


def test_hotkey_listener_signal() -> None:
    """Verify HotkeyListener signal emission."""
    listener = HotkeyListener("ctrl+shift+space")
    spy = MagicMock()
    listener.hotkey_triggered.connect(spy)

    listener._on_triggered()
    spy.assert_called_once()


def test_cli_parser_flags() -> None:
    """Verify CLI argument parser flags."""
    parser = build_parser()

    args_panel = parser.parse_args(["--panel"])
    assert args_panel.panel is True

    args_settings = parser.parse_args(["--settings"])
    assert args_settings.settings is True

    args_daemon = parser.parse_args(["--daemon"])
    assert args_daemon.daemon is True


def test_real_semsearch_app_initialization(tmp_path: Path) -> None:
    """Construct real SemSearchApp with real Database and verify all services wired properly."""
    existing_app = QApplication.instance()
    if existing_app is not None and not isinstance(existing_app, SemSearchApp):
        shiboken6.delete(existing_app)

    db_path = tmp_path / "real_index.db"
    db = Database(db_path)
    code_db_path = tmp_path / "real_code.db"
    code_db = Database(code_db_path)
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)
    cfg_mgr.config.onboarded = True
    cfg_mgr.config.code_enabled = True
    cfg_mgr.save()

    mock_ollama = MagicMock()
    mock_ollama.list_multimodal_models.return_value = ["embeddinggemma-2"]

    app = SemSearchApp(
        ["semsearch", "-platform", "offscreen"],
        config_manager=cfg_mgr,
        db=db,
        code_db=code_db,
        ollama_client=mock_ollama,
    )

    # Verify real services and database linkage
    assert app.db.path == str(db_path)
    assert app.code_db is code_db
    assert app.index_service.db is app.db
    assert app.index_service.config is app.config
    assert app.watcher.index_service is app.index_service
    assert app.watcher.config is app.config
    assert app.scheduler.index_service is app.index_service
    assert app.scheduler.config is app.config
    assert app.panel is not None
    assert not hasattr(app, "window")
    assert app.tray is not None
    assert app.settings_dialog is not None
    assert app.settings_dialog.db is app.db
    assert app.settings_dialog.code_db is code_db

    # Verify reindex_requested signal wiring from settings dialog to app initial crawl
    crawl_mock = MagicMock()
    app._initial_crawl = crawl_mock
    app.settings_dialog.storage_tab.reindex_btn.click()
    if app._crawl_thread is not None:
        app._crawl_thread.join(timeout=2.0)
    crawl_mock.assert_called_once()

    app.shutdown()


class DummyApp:
    """Mock application harness mimicking SemSearchApp with bound methods."""

    def __init__(self, *, code_enabled: bool = True) -> None:
        self.config = AppConfig(code_enabled=code_enabled)
        self.index_service = MagicMock()
        self.watcher = MagicMock()
        self.scheduler = MagicMock()
        self.hotkey_listener = MagicMock()
        self.panel = MagicMock()
        self.settings_dialog = MagicMock()
        self.tray = MagicMock()
        self.ipc_server = None
        self._crawl_thread: threading.Thread | None = None
        self.quit = MagicMock()

    handle_cli_action = SemSearchApp.handle_cli_action
    show_panel = SemSearchApp.show_panel
    show_settings = SemSearchApp.show_settings
    _start_indexing = SemSearchApp._start_indexing
    _trigger_reindex = SemSearchApp._trigger_reindex
    _initial_crawl = SemSearchApp._initial_crawl
    shutdown = SemSearchApp.shutdown


def test_app_cli_action_routing(qapp: QApplication) -> None:
    """Verify action routing to panel, settings dialog, and tray daemon."""
    del qapp
    dummy = DummyApp()
    dummy.handle_cli_action("SHOW_PANEL")
    dummy.panel.show_panel.assert_called_once()

    dummy.handle_cli_action("SHOW_SETTINGS")
    dummy.settings_dialog.load_settings.assert_called_once()

    dummy.handle_cli_action("DAEMON")
    dummy.tray.show.assert_called_once()
    assert dummy.panel.show_panel.call_count == 1


def test_ipc_forwarding_mechanism(qapp: QApplication) -> None:
    """Verify local socket IPC connection and message forwarding."""
    server_name = get_ipc_server_name()
    QLocalServer.removeServer(server_name)
    server = QLocalServer()
    assert server.listen(server_name)

    received_messages: list[str] = []

    def on_new_connection() -> None:
        conn = server.nextPendingConnection()
        if conn:
            if conn.bytesAvailable() == 0:
                conn.waitForReadyRead(200)
            data = conn.readAll().data().decode("utf-8").strip()
            if data:
                received_messages.append(data)

    server.newConnection.connect(on_new_connection)

    # Forward message using IPC helper
    forwarded = forward_ipc_command("SHOW_PANEL")
    assert forwarded is True

    if server.hasPendingConnections() or not received_messages:
        server.waitForNewConnection(200)
    qapp.processEvents()
    assert "SHOW_PANEL" in received_messages

    server.close()
    QLocalServer.removeServer(server_name)


def test_app_start_indexing_triggers_background_crawl() -> None:
    """Verify SemSearchApp._start_indexing triggers background crawl for general and code files."""
    app = DummyApp(code_enabled=True)
    app._start_indexing()
    assert app._crawl_thread is not None
    app._crawl_thread.join(timeout=2.0)

    assert not app._crawl_thread.is_alive()
    assert app.index_service.start.called
    assert app.watcher.start.called
    assert app.scheduler.start.called
    app.index_service.crawl_and_sync.assert_any_call(is_code=False)
    app.index_service.crawl_and_sync.assert_any_call(is_code=True)
    assert app.index_service.crawl_and_sync.call_count == 2


def test_app_start_indexing_skips_code_when_disabled() -> None:
    """Verify _initial_crawl skips code crawl when code_enabled is False."""
    app = DummyApp(code_enabled=False)
    app._start_indexing()
    assert app._crawl_thread is not None
    app._crawl_thread.join(timeout=2.0)

    assert not app._crawl_thread.is_alive()
    app.index_service.crawl_and_sync.assert_called_once_with(is_code=False)


def test_app_start_indexing_does_not_duplicate_running_thread() -> None:
    """Verify _start_indexing does not spawn another thread if already running."""
    app = DummyApp(code_enabled=True)
    thread = threading.Thread(target=lambda: time.sleep(0.1), daemon=True)
    thread.start()
    app._crawl_thread = thread

    app._start_indexing()
    assert app._crawl_thread is thread
    thread.join(timeout=2.0)


def test_app_shutdown_joins_crawl_thread() -> None:
    """Verify shutdown joins running _crawl_thread and resets it to None."""
    app = DummyApp()
    app._crawl_thread = threading.Thread(target=lambda: time.sleep(0.05), daemon=True)
    app._crawl_thread.start()
    assert app._crawl_thread.is_alive()

    app.shutdown()
    assert app._crawl_thread is None
    app.hotkey_listener.stop.assert_called_once()
    app.scheduler.stop.assert_called_once()
    app.watcher.stop.assert_called_once()
    app.index_service.stop.assert_called_once()
    app.quit.assert_called_once()


def test_app_trigger_reindex_spawns_thread() -> None:
    """Verify _trigger_reindex starts crawl thread."""
    app = DummyApp()
    app._trigger_reindex()
    assert app._crawl_thread is not None
    app._crawl_thread.join(timeout=2.0)
    assert not app._crawl_thread.is_alive()
    app.index_service.crawl_and_sync.assert_called()


def test_app_trigger_reindex_rapid_clicks_skips_duplicate() -> None:
    """Verify rapid reindex invocations skip spawning duplicate threads while running."""
    app = DummyApp(code_enabled=True)
    slow_event = threading.Event()
    app.index_service.crawl_and_sync.side_effect = lambda **_kwargs: slow_event.wait(timeout=1.0)

    app._trigger_reindex()
    t1 = app._crawl_thread
    assert t1 is not None
    assert t1.is_alive()

    # Second trigger while thread is still active must skip creating a new thread
    app._trigger_reindex()
    assert app._crawl_thread is t1

    # Unblock thread and allow completion
    slow_event.set()
    t1.join(timeout=2.0)
    assert not t1.is_alive()

    # Subsequent trigger after completion spawns new thread
    slow_event.clear()
    app.index_service.crawl_and_sync.side_effect = None
    app._trigger_reindex()
    t2 = app._crawl_thread
    assert t2 is not None
    assert t2 is not t1
    t2.join(timeout=2.0)
    assert not t2.is_alive()
