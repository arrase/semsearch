"""End-to-end CLI subcommands, single-instance IPC lockfile, and action dispatch tests."""

import json
import tempfile
import time
from collections.abc import Generator
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from semsearch.cli import run_install_desktop, run_query, run_reindex, run_status
from semsearch.config import ConfigManager
from semsearch.core.database import CODE_DB_FILE, MAIN_DB_FILE, Database
from semsearch.core.ollama import OllamaClient
from semsearch.core.types import Candidate, FileKind, FileState, Modality, SegmentKind
from semsearch.core.vectors import float16_to_bytes, l2_normalize
from semsearch.gui.app import (
    SemSearchApp,
    forward_ipc_command,
    get_ipc_runtime_dir,
    get_ipc_server_name,
)
from semsearch.main import build_parser


class _OfflineOllama(OllamaClient):
    """Offline OllamaClient test double returning deterministic 4-dim vectors."""

    def __init__(self) -> None:
        super().__init__(base_url="http://127.0.0.1:11434")

    def unload(self, model: str) -> None:
        assert model

    def embed(
        self,
        model: str,
        inputs: list[str | dict[str, str]],
        *,
        truncate: bool = True,
        num_ctx: int | None = None,
        keep_alive: int | str | None = None,
    ) -> list[list[float]]:
        del model, truncate, num_ctx, keep_alive
        return [[1.0, 0.0, 0.0, 0.0] for _ in inputs]


class _IpcHostHarness(QObject):
    """Minimal QObject binding SemSearchApp IPC lockfile/server lifecycle without replacing QApplication."""

    def __init__(self) -> None:
        super().__init__()
        self.ipc_server: QLocalServer | None = None
        self._instance_lock_fd: int | None = None
        self._ipc_server_name = ""
        self.received: list[str] = []

    def handle_cli_action(self, action: str) -> None:
        self.received.append(action)

    def init_ipc(self) -> None:
        SemSearchApp._init_ipc_server(cast(SemSearchApp, self))

    def close_ipc(self) -> None:
        SemSearchApp._close_ipc_server(cast(SemSearchApp, self))

    def _release_instance_lock(self) -> None:
        SemSearchApp._release_instance_lock(cast(SemSearchApp, self))

    def _on_ipc_connection(self) -> None:
        SemSearchApp._on_ipc_connection(cast(SemSearchApp, self))

    @Slot()
    def _on_ipc_ready_read(self) -> None:
        SemSearchApp._on_ipc_ready_read(cast(SemSearchApp, self))

    def _handle_ipc_message(self, client: QLocalSocket) -> None:
        SemSearchApp._handle_ipc_message(cast(SemSearchApp, self), client)


def _flush_qt_events(qapp: QApplication) -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _wait_for_ipc(qapp: QApplication, received: list[str], expected_count: int, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while len(received) < expected_count and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


@pytest.fixture
def isolated_ipc_dir(monkeypatch: pytest.MonkeyPatch) -> Generator[Path, None, None]:
    """Provide a short temporary XDG_RUNTIME_DIR within the AF_UNIX path limit."""
    with tempfile.TemporaryDirectory(prefix="ss_e2e_") as raw_dir:
        runtime_base = Path(raw_dir)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_base))
        yield runtime_base


def _seed_cli_workspace(tmp_path: Path) -> tuple[ConfigManager, Path, Path]:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    doc_file = docs_dir / "quarterly_budget.txt"
    doc_file.write_text("Quarterly budget report and fiscal forecast", encoding="utf-8")

    cfg_manager = ConfigManager(tmp_path / "config.json")
    cfg_manager.config.folders = [str(docs_dir)]
    cfg_manager.config.code_enabled = True
    cfg_manager.config.code_folders = []
    cfg_manager.save()

    data_dir = tmp_path / "data"
    vec = float16_to_bytes(l2_normalize(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)))
    with Database(data_dir / MAIN_DB_FILE) as db:
        db.set_meta("embedding_model", "embeddinggemma-2")
        db.set_meta("embedding_dim", "4")
        fid = db.insert_file(Candidate(str(doc_file), FileKind.DOC, 64, 1700000000.0, 1, 1))
        db.insert_segment(
            fid,
            SegmentKind.CHUNK,
            Modality.TEXT,
            loc=1.0,
            excerpt="Quarterly budget report",
            body="Quarterly budget report and fiscal forecast",
            vec=vec,
        )
        db.update_file_state(fid, FileState.DONE)
    with Database(data_dir / CODE_DB_FILE):
        pass

    return cfg_manager, data_dir, doc_file


def test_cli_flags_and_subcommands_parsing() -> None:
    parser = build_parser()

    args_default = parser.parse_args([])
    assert args_default.command is None
    assert args_default.panel is False
    assert args_default.toggle is False
    assert args_default.settings is False
    assert args_default.daemon is False

    assert parser.parse_args(["--panel"]).panel is True
    assert parser.parse_args(["--toggle"]).toggle is True
    assert parser.parse_args(["--settings"]).settings is True
    assert parser.parse_args(["--daemon"]).daemon is True

    args_query = parser.parse_args(
        ["query", "quarterly", "report", "--code", "--kind", "pdf", "--limit", "5", "--json"]
    )
    assert args_query.command == "query"
    assert args_query.query == ["quarterly", "report"]
    assert args_query.code is True
    assert args_query.kind == ["pdf"]
    assert args_query.limit == 5
    assert args_query.json is True

    args_status = parser.parse_args(["status", "--json"])
    assert args_status.command == "status"
    assert args_status.json is True

    args_reindex = parser.parse_args(["reindex", "--force"])
    assert args_reindex.command == "reindex"
    assert args_reindex.force is True

    args_install = parser.parse_args(["install-desktop", "--data-home", "/tmp/semsearch_xdg"])
    assert args_install.command == "install-desktop"
    assert args_install.data_home == Path("/tmp/semsearch_xdg")


def test_single_instance_lockfile_prevents_double_daemon(
    qapp: QApplication,
    isolated_ipc_dir: Path,
) -> None:
    del isolated_ipc_dir
    primary = _IpcHostHarness()
    secondary = _IpcHostHarness()

    try:
        primary.init_ipc()
        assert primary.ipc_server is not None
        assert primary._instance_lock_fd is not None
        assert (get_ipc_runtime_dir() / "instance.lock").is_file()

        # A second daemon attempting to acquire the exclusive lockfile must abort cleanly
        secondary.init_ipc()
        assert secondary.ipc_server is None
        assert secondary._instance_lock_fd is None

        # Primary daemon socket remains operational and continues receiving forwarded IPC commands
        assert forward_ipc_command("TOGGLE") is True
        _wait_for_ipc(qapp, primary.received, 1)
        assert primary.received == ["TOGGLE"]
    finally:
        secondary.close_ipc()
        primary.close_ipc()
        _flush_qt_events(qapp)

    # Once primary shuts down and releases instance.lock, a new daemon can claim ownership
    restarted = _IpcHostHarness()
    try:
        restarted.init_ipc()
        assert restarted.ipc_server is not None
        assert restarted._instance_lock_fd is not None
    finally:
        restarted.close_ipc()
        _flush_qt_events(qapp)


def test_ipc_command_forwarding_actions(
    qapp: QApplication,
    isolated_ipc_dir: Path,
) -> None:
    del isolated_ipc_dir
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
            data = bytes(socket.readAll().data()).decode("utf-8").strip()
            if data:
                actions_received.append(data)
            socket.write(b"OK\n")
            socket.flush()
            socket.disconnectFromServer()

    server.newConnection.connect(on_new_connection)

    expected_actions = ["PANEL", "TOGGLE", "SETTINGS", "DAEMON", "REINDEX", "REINDEX_FORCE"]
    try:
        for action in expected_actions:
            assert forward_ipc_command(action) is True
            qapp.processEvents()
    finally:
        server.close()
        QLocalServer.removeServer(server_name)
        _flush_qt_events(qapp)

    assert actions_received == expected_actions


def test_app_action_dispatch_on_ipc_messages() -> None:
    app_stub = MagicMock(spec=SemSearchApp)
    app_stub.tray = MagicMock()
    app_stub.handle_cli_action = SemSearchApp.handle_cli_action.__get__(app_stub)

    app_stub.handle_cli_action("SHOW_PANEL")
    app_stub.show_panel.assert_called_once()

    app_stub.handle_cli_action("TOGGLE")
    app_stub.toggle_panel.assert_called_once()

    app_stub.handle_cli_action("SHOW_SETTINGS")
    app_stub.show_settings.assert_called_once()

    app_stub.handle_cli_action("REINDEX")
    app_stub._trigger_reindex.assert_called_once()

    app_stub.handle_cli_action("REINDEX_FORCE")
    app_stub._trigger_force_reindex.assert_called_once()

    app_stub.handle_cli_action("DAEMON")
    assert app_stub.show_panel.call_count == 1
    assert app_stub.show_settings.call_count == 1


def test_cli_query_and_status_end_to_end(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg_manager, data_dir, doc_file = _seed_cli_workspace(tmp_path)

    with _OfflineOllama() as ollama:
        rc = run_query(
            "quarterly budget",
            as_json=True,
            config_manager=cfg_manager,
            data_dir=data_dir,
            ollama_client=ollama,
        )
    assert rc == 0
    hits = json.loads(capsys.readouterr().out)
    assert len(hits) == 1
    assert hits[0]["path"] == str(doc_file)
    assert hits[0]["kind"] == "doc"

    rc = run_status(as_json=True, config_manager=cfg_manager, data_dir=data_dir)
    assert rc == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["configured_model"] == "embeddinggemma-2"
    assert status_payload["main"]["files"] == 1
    assert status_payload["main"]["vectors"] == 1
    assert status_payload["main"]["embedding_dim"] == 4


def test_cli_reindex_and_install_desktop_end_to_end(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    qapp: QApplication,
    isolated_ipc_dir: Path,
) -> None:
    del isolated_ipc_dir
    cfg_manager, data_dir, _ = _seed_cli_workspace(tmp_path)

    with _OfflineOllama() as ollama:
        rc = run_reindex(force=True, config_manager=cfg_manager, data_dir=data_dir, ollama_client=ollama)
    assert rc == 0
    assert "Reset 1 files" in capsys.readouterr().out

    daemon_host = _IpcHostHarness()
    try:
        daemon_host.init_ipc()
        rc = run_reindex(force=False, forward_ipc=forward_ipc_command)
        _wait_for_ipc(qapp, daemon_host.received, 1)
        assert rc == 0
        assert "Sent REINDEX" in capsys.readouterr().out
        assert daemon_host.received == ["REINDEX"]
    finally:
        daemon_host.close_ipc()
        _flush_qt_events(qapp)

    xdg_data = tmp_path / "xdg_data"
    rc = run_install_desktop(data_home=xdg_data, exec_path="/usr/bin/semsearch")
    assert rc == 0
    install_out = capsys.readouterr().out
    assert "Installed SemSearch desktop integration files" in install_out
    assert (xdg_data / "applications" / "semsearch.desktop").is_file()
    assert (xdg_data / "icons" / "hicolor" / "scalable" / "apps" / "semsearch.svg").is_file()
    assert (xdg_data / "dbus-1" / "services" / "org.semsearch.DesktopSearch.service").is_file()
    assert (xdg_data / "krunner" / "dbusplugins" / "semsearch.desktop").is_file()
    assert (xdg_data / "gnome-shell" / "search-providers" / "semsearch-search-provider.ini").is_file()
