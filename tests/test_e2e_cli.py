"""End-to-end CLI activation and single-instance IPC dispatch tests."""

from unittest.mock import MagicMock

from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication

from semsearch.gui.app import (
    SemSearchApp,
    forward_ipc_command,
    get_ipc_server_name,
)
from semsearch.main import build_parser


def test_cli_flags_parsing() -> None:
    parser = build_parser()

    # Default invocation without flags
    args_default = parser.parse_args([])
    assert args_default.panel is False
    assert args_default.settings is False
    assert args_default.daemon is False

    # Individual flags
    assert parser.parse_args(["--panel"]).panel is True
    assert parser.parse_args(["--settings"]).settings is True
    assert parser.parse_args(["--daemon"]).daemon is True


def test_single_instance_socket_lock(qapp: QApplication) -> None:
    server_name = get_ipc_server_name()
    QLocalServer.removeServer(server_name)

    server1 = QLocalServer()
    assert server1.listen(server_name) is True

    # Attempting to listen on the same socket name from a second server must collide / fail
    server2 = QLocalServer()
    assert server2.listen(server_name) is False

    server1.close()
    server2.close()
    QLocalServer.removeServer(server_name)


def test_ipc_command_forwarding_actions(qapp: QApplication) -> None:
    server_name = get_ipc_server_name()
    QLocalServer.removeServer(server_name)

    server = QLocalServer()
    assert server.listen(server_name) is True

    actions_received: list[str] = []

    def on_new_connection() -> None:
        socket = server.nextPendingConnection()
        if socket:
            if socket.bytesAvailable() == 0:
                socket.waitForReadyRead(500)
            data = socket.readAll().data().decode("utf-8").strip()
            if data:
                actions_received.append(data)
            socket.write(b"OK\n")
            socket.flush()
            socket.disconnectFromServer()

    server.newConnection.connect(on_new_connection)

    # Test forwarding each supported action command
    for action in ("SHOW_PANEL", "SHOW_SETTINGS", "DAEMON"):
        forwarded = forward_ipc_command(action)
        assert forwarded is True
        qapp.processEvents()

    assert actions_received == ["SHOW_PANEL", "SHOW_SETTINGS", "DAEMON"]

    server.close()
    QLocalServer.removeServer(server_name)


def test_app_action_dispatch_on_ipc_messages() -> None:
    app_stub = MagicMock(spec=SemSearchApp)
    app_stub.tray = MagicMock()
    app_stub.handle_cli_action = SemSearchApp.handle_cli_action.__get__(app_stub)

    app_stub.handle_cli_action("SHOW_PANEL")
    app_stub.show_panel.assert_called_once()

    app_stub.handle_cli_action("SHOW_SETTINGS")
    app_stub.show_settings.assert_called_once()

    app_stub.handle_cli_action("DAEMON")
    # Daemon runs silently without popping open dialogs
    assert app_stub.show_panel.call_count == 1
    assert app_stub.show_settings.call_count == 1
