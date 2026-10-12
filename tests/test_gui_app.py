"""Unit and integration tests for SemSearchApp shell, IPC server, hotkeys, and CLI dispatcher."""

from pathlib import Path
from unittest.mock import MagicMock
import shiboken6
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication

from semsearch.config import ConfigManager
from semsearch.core.database import Database
from semsearch.gui.app import (
    SemSearchApp,
    forward_ipc_command,
    get_ipc_server_name,
)
from semsearch.gui.hotkey import HotkeyListener, normalize_hotkey
from semsearch.main import build_parser


def test_normalize_hotkey():
    """Verify conversion to pynput format."""
    assert normalize_hotkey("ctrl+shift+space") == "<ctrl>+<shift>+<space>"
    assert normalize_hotkey("<ctrl>+<shift>+<space>") == "<ctrl>+<shift>+<space>"
    assert normalize_hotkey("ctrl+alt+k") == "<ctrl>+<alt>+<k>"


def test_hotkey_listener_signal():
    """Verify HotkeyListener signal emission."""
    listener = HotkeyListener("ctrl+shift+space")
    spy = MagicMock()
    listener.hotkey_triggered.connect(spy)

    listener._on_triggered()
    spy.assert_called_once()


def test_cli_parser_flags():
    """Verify CLI argument parser flags."""
    parser = build_parser()

    args_panel = parser.parse_args(["--panel"])
    assert args_panel.panel is True
    assert args_panel.window is False

    args_window = parser.parse_args(["--window"])
    assert args_window.window is True

    args_settings = parser.parse_args(["--settings"])
    assert args_settings.settings is True

    args_daemon = parser.parse_args(["--daemon"])
    assert args_daemon.daemon is True


def test_real_semsearch_app_initialization(tmp_path: Path):
    """Construct real SemSearchApp with real Database and verify all services wired properly."""
    existing_app = QApplication.instance()
    if existing_app is not None and not isinstance(existing_app, SemSearchApp):
        shiboken6.delete(existing_app)

    db_path = tmp_path / "real_index.db"
    db = Database(db_path)
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)
    cfg_mgr.config.onboarded = True
    cfg_mgr.save()

    mock_ollama = MagicMock()
    mock_ollama.list_multimodal_models.return_value = ["embeddinggemma-2"]

    app = SemSearchApp(
        ["semsearch", "-platform", "offscreen"],
        config_manager=cfg_mgr,
        db=db,
        ollama_client=mock_ollama,
    )

    # Verify real services and database linkage
    assert app.db.path == str(db_path)
    assert app.db.db_path == str(db_path)
    assert app.index_service.db is app.db
    assert app.index_service.config is app.config
    assert app.watcher.index_service is app.index_service
    assert app.watcher.config is app.config
    assert app.scheduler.index_service is app.index_service
    assert app.scheduler.config is app.config
    assert app.panel is not None
    assert app.window is not None
    assert app.tray is not None
    assert app.settings_dialog is not None
    assert app.settings_dialog.db is app.db

    app.shutdown()


def test_app_cli_action_routing(tmp_path: Path, qapp: QApplication):
    """Verify action routing to panel, window, and settings dialogs."""
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)
    db_file = tmp_path / "test.db"
    db = Database(db_file)
    mock_ollama = MagicMock()
    mock_ollama.list_multimodal_models.return_value = ["embeddinggemma-2"]

    # We use a custom test app without subclassing QApplication again since qapp is already running
    # Test method handle_cli_action on mock or standalone instance
    panel = MagicMock()
    window = MagicMock()
    settings_dialog = MagicMock()

    class DummyApp:
        def __init__(self):
            self.panel = panel
            self.window = window
            self.settings_dialog = settings_dialog
            self.tray = MagicMock()

        handle_cli_action = SemSearchApp.handle_cli_action
        show_panel = SemSearchApp.show_panel
        show_window = SemSearchApp.show_window
        show_settings = SemSearchApp.show_settings

    dummy = DummyApp()
    dummy.handle_cli_action("SHOW_PANEL")
    panel.show_panel.assert_called_once()

    dummy.handle_cli_action("SHOW_WINDOW")
    window.show.assert_called_once()

    dummy.handle_cli_action("SHOW_SETTINGS")
    settings_dialog.load_settings.assert_called_once()


def test_ipc_forwarding_mechanism(tmp_path: Path, qapp: QApplication):
    """Verify local socket IPC connection and message forwarding."""
    server_name = get_ipc_server_name()
    QLocalServer.removeServer(server_name)
    server = QLocalServer()
    assert server.listen(server_name)

    received_messages = []

    def on_new_connection():
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
