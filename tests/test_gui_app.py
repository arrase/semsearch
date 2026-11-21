"""Unit and integration tests for SemSearchApp shell, IPC lockfile/socket, hotkeys, and CLI entry point."""

import fcntl
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import weakref
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, create_autospec

import pytest
from pynput.keyboard import HotKey
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, Slot
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtWidgets import QApplication, QDialog, QSystemTrayIcon

import semsearch.gui.app as app_module
import semsearch.main as main_module
from semsearch.config import ConfigManager
from semsearch.core.database import CODE_DB_FILE, Database
from semsearch.core.ollama import OllamaClient
from semsearch.gui.app import (
    SemSearchApp,
    forward_ipc_command,
    get_ipc_runtime_dir,
    get_ipc_server_name,
)
from semsearch.gui.desktop_providers import DesktopSearchProviders
from semsearch.gui.hotkey import HotkeyListener, normalize_hotkey
from semsearch.gui.panel import SearchPanel
from semsearch.gui.settings import SettingsDialog
from semsearch.gui.tray import SystemTrayManager
from semsearch.i18n import get_language, set_language
from semsearch.main import build_parser, main
from semsearch.services.indexer import IndexService
from semsearch.services.scheduler import PowerScheduler
from semsearch.services.searcher import SearchService
from semsearch.services.watcher import FileSystemWatcher

_BOUND_APP_METHODS = (
    "_apply_language",
    "_init_ipc_server",
    "_release_instance_lock",
    "_close_ipc_server",
    "_on_ipc_connection",
    "_handle_ipc_message",
    "_set_code_db",
    "_sync_code_db",
    "_sync_hotkey_status",
    "_start_hotkey_listener",
    "_on_settings_saved",
    "_open_panel_with_query",
    "handle_cli_action",
    "toggle_panel",
    "show_panel",
    "show_settings",
    "start",
    "_on_onboarding_finished",
    "_start_indexing",
    "_trigger_force_reindex",
    "_trigger_reindex",
    "_crawl_loop",
    "_initial_crawl",
    "shutdown",
)


class _AppHarness(QObject):
    """QObject stand-in binding real SemSearchApp methods without replacing session QApplication."""

    def __init__(self) -> None:
        super().__init__()
        self.layout_direction = Qt.LayoutDirection.LeftToRight
        self.quit = MagicMock()

    def setLayoutDirection(self, direction: Qt.LayoutDirection) -> None:  # noqa: N802
        self.layout_direction = direction

    @Slot()
    def _on_ipc_ready_read(self) -> None:
        SemSearchApp._on_ipc_ready_read(cast(SemSearchApp, self))


for _method_name in _BOUND_APP_METHODS:
    setattr(_AppHarness, _method_name, getattr(SemSearchApp, _method_name))


@dataclass
class _HarnessBundle:
    """Strongly typed SemSearchApp harness plus mock handles for service assertions."""

    app: SemSearchApp
    db: Database
    ollama: MagicMock
    search_service: MagicMock
    index_service: MagicMock
    watcher: MagicMock
    scheduler: MagicMock
    tray: MagicMock
    panel: MagicMock
    settings_dialog: MagicMock
    desktop_providers: MagicMock
    hotkey_listener: MagicMock
    quit_spy: MagicMock


@pytest.fixture
def short_ipc_dir(monkeypatch: pytest.MonkeyPatch) -> Generator[Path, None, None]:
    """Provide a short temporary XDG_RUNTIME_DIR well within the 108-byte AF_UNIX path limit."""
    with tempfile.TemporaryDirectory(prefix="ss_ipc_") as raw_dir:
        runtime_base = Path(raw_dir)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_base))
        yield runtime_base


def _make_mock_ollama() -> MagicMock:
    client = cast(MagicMock, create_autospec(OllamaClient, instance=True))
    client.base_url = "http://localhost:11434"
    client.num_ctx = 0
    return client


def _init_harness_runtime_fields(app: SemSearchApp) -> None:
    app.ipc_server = None
    app._instance_lock_fd = None
    app._ipc_server_name = ""
    app._crawl_thread = None
    app._crawl_lock = threading.Lock()
    app._crawl_active = False
    app._reindex_pending = False
    app.is_shutdown = False


def _make_harness(
    tmp_path: Path,
    *,
    code_enabled: bool = True,
    onboarded: bool = True,
    init_ipc: bool = False,
) -> _HarnessBundle:
    cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")
    cfg_mgr.config.code_enabled = code_enabled
    cfg_mgr.config.onboarded = onboarded
    cfg_mgr.save()

    db = Database(tmp_path / "main.db")
    code_db = Database(tmp_path / "code.db") if code_enabled else None
    ollama = _make_mock_ollama()

    search_service = cast(MagicMock, create_autospec(SearchService, instance=True))
    search_service.db = db
    search_service.code_db = code_db
    search_service.model = cfg_mgr.config.model

    index_service = cast(MagicMock, create_autospec(IndexService, instance=True))
    index_service.db = db
    index_service.code_db = code_db

    watcher = cast(MagicMock, create_autospec(FileSystemWatcher, instance=True))
    scheduler = cast(MagicMock, create_autospec(PowerScheduler, instance=True))
    tray = cast(MagicMock, create_autospec(SystemTrayManager, instance=True))
    panel = cast(MagicMock, create_autospec(SearchPanel, instance=True))
    panel.isVisible.return_value = False

    settings_dialog = cast(MagicMock, create_autospec(SettingsDialog, instance=True))
    settings_dialog.code_db = code_db
    settings_dialog.storage_tab = MagicMock()
    settings_dialog.storage_tab.code_db = code_db

    desktop_providers = cast(MagicMock, create_autospec(DesktopSearchProviders, instance=True))
    hotkey_listener = cast(MagicMock, create_autospec(HotkeyListener, instance=True))
    hotkey_listener.backend_name = "pynput"
    hotkey_listener.last_error = ""
    hotkey_listener.status_changed = MagicMock()

    raw = _AppHarness()
    app = cast(SemSearchApp, raw)
    app.config_manager = cfg_mgr
    app.config = cfg_mgr.config
    app.db = db
    app.code_db = code_db
    app.ollama = ollama
    app.search_service = search_service
    app.index_service = index_service
    app.watcher = watcher
    app.scheduler = scheduler
    app.tray = tray
    app.panel = panel
    app.settings_dialog = settings_dialog
    app.desktop_providers = desktop_providers
    app.hotkey_listener = hotkey_listener
    _init_harness_runtime_fields(app)

    if init_ipc:
        app._init_ipc_server()

    return _HarnessBundle(
        app=app,
        db=db,
        ollama=ollama,
        search_service=search_service,
        index_service=index_service,
        watcher=watcher,
        scheduler=scheduler,
        tray=tray,
        panel=panel,
        settings_dialog=settings_dialog,
        desktop_providers=desktop_providers,
        hotkey_listener=hotkey_listener,
        quit_spy=raw.quit,
    )


def _process_until(qapp: QApplication, predicate: object, timeout_sec: float = 2.0) -> bool:
    assert callable(predicate)
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    qapp.processEvents()
    return bool(predicate())


def _install_fake_hotkey_factory(monkeypatch: pytest.MonkeyPatch) -> list[MagicMock]:
    created_hotkeys: list[MagicMock] = []

    def fake_hotkey_cls(shortcut: str, parent: QObject | None = None) -> MagicMock:
        del parent
        listener = MagicMock()
        listener.shortcut = shortcut
        listener.backend_name = "portal"
        listener.last_error = ""
        listener.hotkey_triggered = MagicMock()
        listener.status_changed = MagicMock()
        created_hotkeys.append(listener)
        return listener

    monkeypatch.setattr(app_module, "HotkeyListener", fake_hotkey_cls)
    return created_hotkeys


def _run_real_app_in_subprocess(tmp_path: Path, cfg_file: Path, short_ipc_dir: Path, data_home: Path) -> None:
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["XDG_RUNTIME_DIR"] = str(short_ipc_dir)
    env["XDG_DATA_HOME"] = str(data_home)
    script = f"""
from pathlib import Path
from unittest.mock import MagicMock
from semsearch.config import ConfigManager
from semsearch.core.database import Database
from semsearch.gui.app import SemSearchApp

cfg_mgr = ConfigManager(config_path=Path({str(cfg_file)!r}))
db = Database(Path({str(tmp_path / "sub_index.db")!r}))
code_db = Database(Path({str(tmp_path / "sub_code.db")!r}))
ollama = MagicMock()
app = SemSearchApp(
    ["semsearch", "-platform", "offscreen"],
    config_manager=cfg_mgr,
    db=db,
    code_db=code_db,
    ollama_client=ollama,
)
assert app.applicationName() == "SemSearch"
assert app.desktopFileName() == "semsearch"
assert not app.windowIcon().isNull()
assert "QMenu" in app.styleSheet()
assert app.ipc_server is not None
assert app.desktop_providers is not None
assert app.hotkey_listener.parent() is app
reset_mock = MagicMock(return_value=0)
crawl_mock = MagicMock()
app.index_service.reset_all_for_reindex = reset_mock
app._initial_crawl = crawl_mock
app.settings_dialog.force_reindex_requested.emit()
if app._crawl_thread is not None:
    app._crawl_thread.join(timeout=2.0)
reset_mock.assert_called_once()
crawl_mock.assert_called_once()
app.shutdown()
ollama.close.assert_called_once()
"""
    subprocess.run([sys.executable, "-c", script], env=env, check=True)


def test_real_semsearch_app_initialization(
    tmp_path: Path,
    short_ipc_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify full SemSearchApp construction, metadata, theme, providers, signal wiring, and clean shutdown."""
    data_home = tmp_path / "data_home"
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)
    cfg_mgr.config.onboarded = True
    cfg_mgr.config.code_enabled = True
    cfg_mgr.save()

    if QApplication.instance() is not None:
        _run_real_app_in_subprocess(tmp_path, cfg_file, short_ipc_dir, data_home)
        return

    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    db = Database(tmp_path / "real_index.db")
    code_db = Database(tmp_path / "real_code.db")
    mock_ollama = _make_mock_ollama()

    app = SemSearchApp(
        ["semsearch", "-platform", "offscreen"],
        config_manager=cfg_mgr,
        db=db,
        code_db=code_db,
        ollama_client=mock_ollama,
    )
    try:
        assert app.applicationName() == "SemSearch"
        assert app.desktopFileName() == "semsearch"
        assert not app.windowIcon().isNull()
        assert "QMenu" in app.styleSheet()
        assert app.db is db
        assert app.code_db is code_db
        assert app.index_service.db is db
        assert app.desktop_providers is not None
        assert app.hotkey_listener.parent() is app
        assert app.ipc_server is not None

        reset_mock = MagicMock(return_value=0)
        crawl_mock = MagicMock()
        monkeypatch.setattr(app.index_service, "reset_all_for_reindex", reset_mock)
        monkeypatch.setattr(app, "_initial_crawl", crawl_mock)
        app.settings_dialog.force_reindex_requested.emit()
        if app._crawl_thread is not None:
            app._crawl_thread.join(timeout=2.0)
        reset_mock.assert_called_once()
        crawl_mock.assert_called_once()

        show_spy = MagicMock()
        search_spy = MagicMock()
        monkeypatch.setattr(app.panel, "show_panel", show_spy)
        monkeypatch.setattr(app.panel, "execute_search", search_spy)
        app.desktop_providers.launch_panel_requested.emit("quarterly report")
        show_spy.assert_called_once()
        search_spy.assert_called_once_with("quarterly report")
    finally:
        app.shutdown()
        app.setStyleSheet("")
        app.setLayoutDirection(Qt.LayoutDirection.LeftToRight)


def test_normalize_hotkey() -> None:
    """Verify conversion to pynput format, modifier aliases, and HotKey.parse compatibility."""
    assert normalize_hotkey("ctrl+shift+space") == "<ctrl>+<shift>+<space>"
    assert normalize_hotkey("<ctrl>+<shift>+<space>") == "<ctrl>+<shift>+<space>"
    assert normalize_hotkey("ctrl+alt+k") == "<ctrl>+<alt>+k"
    assert normalize_hotkey("<ctrl>+<alt>+<k>") == "<ctrl>+<alt>+k"
    assert normalize_hotkey("super+space") == "<cmd>+<space>"

    for combo in ("ctrl+shift+f", "ctrl+alt+space", "ctrl+1", "ctrl+alt+k", "meta+k"):
        parsed = HotKey.parse(normalize_hotkey(combo))
        assert len(parsed) > 0


def test_hotkey_listener_signal(qapp: QApplication) -> None:
    """Verify HotkeyListener signal emission."""
    del qapp
    listener = HotkeyListener("ctrl+shift+space")
    spy = MagicMock()
    listener.hotkey_triggered.connect(spy)

    listener._on_triggered()
    spy.assert_called_once()


def test_ipc_runtime_dir_uses_xdg_or_tempdir_with_0700(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify get_ipc_runtime_dir respects valid XDG_RUNTIME_DIR and falls back with 0700 permissions."""
    xdg_base = tmp_path / "xdg_run"
    xdg_base.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg_base))

    runtime_dir = get_ipc_runtime_dir()
    assert runtime_dir == xdg_base / "semsearch"
    assert stat.S_IMODE(runtime_dir.stat().st_mode) == 0o700
    assert get_ipc_server_name() == str(runtime_dir / "ipc.sock")

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "does_not_exist"))
    fallback_dir = get_ipc_runtime_dir()
    assert fallback_dir == Path(tempfile.gettempdir()) / f"semsearch_{os.getuid()}"
    assert stat.S_IMODE(fallback_dir.stat().st_mode) == 0o700


def test_forward_ipc_command_returns_false_without_server(short_ipc_dir: Path) -> None:
    """Verify AF_UNIX forward_ipc_command returns False cleanly when no daemon is listening."""
    del short_ipc_dir
    assert forward_ipc_command("TOGGLE", timeout=0.2) is False


def test_ipc_lockfile_prevents_stealing_socket_and_delivers_commands(
    qapp: QApplication,
    tmp_path: Path,
    short_ipc_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify flock prevents second instance from removing primary socket and AF_UNIX delivers messages."""
    del short_ipc_dir
    primary = _make_harness(tmp_path / "p1", init_ipc=True)
    received: list[str] = []
    monkeypatch.setattr(primary.app, "handle_cli_action", received.append)

    try:
        assert primary.app.ipc_server is not None
        assert primary.app._instance_lock_fd is not None
        lock_file = get_ipc_runtime_dir() / "instance.lock"
        sock_file = Path(get_ipc_server_name())
        assert stat.S_IMODE(lock_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(sock_file.stat().st_mode) == 0o600

        # A second instance must fail flock without calling removeServer or stealing the socket.
        secondary = _make_harness(tmp_path / "p2", init_ipc=True)
        assert secondary.app.ipc_server is None
        assert secondary.app._instance_lock_fd is None
        assert sock_file.is_socket()

        # Shutting down the unowned secondary instance must leave the primary socket intact.
        secondary.app.shutdown()
        assert sock_file.is_socket()

        # Real AF_UNIX messages reach primary, and oversized payloads are bounded to 4096 bytes.
        assert forward_ipc_command("TOGGLE") is True
        assert forward_ipc_command("REINDEX_FORCE") is True
        assert forward_ipc_command("X" * 9000) is True
        assert _process_until(qapp, lambda: len(received) == 3)
        assert received[0] == "TOGGLE"
        assert received[1] == "REINDEX_FORCE"
        assert len(received[2]) == 4096
    finally:
        primary.app.shutdown()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()

    # After primary shutdown, lock is released and socket is removed so a new instance can bind.
    assert not Path(get_ipc_server_name()).exists()
    lock_fd = os.open(get_ipc_runtime_dir() / "instance.lock", os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def test_ipc_ready_read_teardown_without_flushing_deferred_deletes(
    qapp: QApplication,
    tmp_path: Path,
    short_ipc_dir: Path,
) -> None:
    """Verify dropping an IPC harness after readyRead without flushing DeferredDelete never crashes."""
    del short_ipc_dir

    def run_ipc_session() -> tuple[MagicMock, weakref.ref[SemSearchApp]]:
        bundle = _make_harness(tmp_path / "ipc_teardown", init_ipc=True)
        try:
            server = bundle.app.ipc_server
            assert server is not None
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client_sock:
                client_sock.connect(get_ipc_server_name())
                assert _process_until(
                    qapp,
                    lambda: bool(server.findChildren(QLocalSocket)) and not server.hasPendingConnections(),
                )
                client_sock.sendall(b"TOGGLE")
                assert _process_until(qapp, lambda: bundle.panel.show_panel.call_count == 1)
        finally:
            bundle.app.shutdown()
        return bundle.panel, weakref.ref(bundle.app)

    panel, app_ref = run_ipc_session()
    assert app_ref() is None
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    panel.show_panel.assert_called_once()


def test_handle_cli_action_routes_all_commands(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify TOGGLE, PANEL, SETTINGS, REINDEX, REINDEX_FORCE, and DAEMON routing."""
    del qapp
    bundle = _make_harness(tmp_path)
    reindex_spy = MagicMock()
    force_reindex_spy = MagicMock()
    monkeypatch.setattr(bundle.app, "_trigger_reindex", reindex_spy)
    monkeypatch.setattr(bundle.app, "_trigger_force_reindex", force_reindex_spy)

    try:
        # TOGGLE shows when hidden, hides when visible
        bundle.panel.isVisible.return_value = False
        bundle.app.handle_cli_action("TOGGLE")
        bundle.panel.show_panel.assert_called_once()

        bundle.panel.isVisible.return_value = True
        bundle.app.handle_cli_action("TOGGLE")
        bundle.panel.hide_panel.assert_called_once()

        # PANEL / SHOW_PANEL
        bundle.app.handle_cli_action("PANEL")
        bundle.app.handle_cli_action("SHOW_PANEL")
        assert bundle.panel.show_panel.call_count == 3

        # SETTINGS / SHOW_SETTINGS
        bundle.app.handle_cli_action("SETTINGS")
        bundle.app.handle_cli_action("SHOW_SETTINGS")
        assert bundle.settings_dialog.load_settings.call_count == 2
        assert bundle.settings_dialog.show.call_count == 2

        # REINDEX / REINDEX_FORCE
        bundle.app.handle_cli_action("REINDEX")
        reindex_spy.assert_called_once()

        bundle.app.handle_cli_action("REINDEX_FORCE")
        force_reindex_spy.assert_called_once()

        # DAEMON respects QSystemTrayIcon.isSystemTrayAvailable()
        monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
        bundle.app.handle_cli_action("DAEMON")
        bundle.tray.show.assert_not_called()

        monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
        bundle.app.handle_cli_action("DAEMON")
        bundle.tray.show.assert_called_once()
    finally:
        bundle.app.shutdown()


def test_on_settings_saved_hot_reloads_services_and_enables_code_db(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify _on_settings_saved applies language, ollama, model, enables code_db, wakes scheduler, and hotkey."""
    del qapp
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg_data"))
    created_hotkeys = _install_fake_hotkey_factory(monkeypatch)

    bundle = _make_harness(tmp_path, code_enabled=False)
    reindex_spy = MagicMock()
    monkeypatch.setattr(bundle.app, "_trigger_reindex", reindex_spy)

    try:
        initial_code_db = bundle.app.code_db
        assert initial_code_db is None
        bundle.app.config.language = "es"
        bundle.app.config.ollama_url = "http://127.0.0.1:11435"
        bundle.app.config.num_ctx = 4096
        bundle.app.config.model = "multimodal-v2"
        bundle.app.config.code_enabled = True
        bundle.app.config.hotkey = "ctrl+alt+k"

        bundle.app._on_settings_saved()

        assert get_language() == "es"
        assert bundle.ollama.base_url == "http://127.0.0.1:11435"
        assert bundle.ollama.num_ctx == 4096
        assert bundle.search_service.model == "multimodal-v2"

        opened_code_db = bundle.app.code_db
        assert opened_code_db is not None
        assert Path(opened_code_db.path) == tmp_path / "xdg_data" / "semsearch" / CODE_DB_FILE
        assert bundle.search_service.code_db is opened_code_db
        assert bundle.index_service.code_db is opened_code_db
        assert bundle.settings_dialog.code_db is opened_code_db
        assert bundle.settings_dialog.storage_tab.code_db is opened_code_db

        bundle.tray.retranslate_ui.assert_called_once()
        bundle.panel.retranslate_ui.assert_called_once()
        bundle.settings_dialog.retranslate_ui.assert_called_once()
        bundle.index_service.sync_model_state.assert_called_once()
        bundle.scheduler.wake.assert_called_once()
        bundle.watcher.stop.assert_called_once()
        bundle.watcher.start.assert_called_once()
        assert len(created_hotkeys) == 1
        assert created_hotkeys[0].shortcut == "ctrl+alt+k"
        created_hotkeys[0].start.assert_called_once()
        bundle.settings_dialog.set_hotkey_status.assert_called_once_with("portal", "")
        reindex_spy.assert_called_once()
    finally:
        bundle.app.shutdown()


def test_on_settings_saved_disables_and_closes_code_db(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify disabling code_enabled in _on_settings_saved closes code_db and clears all references."""
    del qapp
    _install_fake_hotkey_factory(monkeypatch)
    bundle = _make_harness(tmp_path, code_enabled=True)
    reindex_spy = MagicMock()
    monkeypatch.setattr(bundle.app, "_trigger_reindex", reindex_spy)

    try:
        opened_code_db = bundle.app.code_db
        assert opened_code_db is not None
        close_spy = MagicMock(wraps=opened_code_db.close)
        monkeypatch.setattr(opened_code_db, "close", close_spy)

        bundle.app.config.code_enabled = False
        bundle.app._on_settings_saved()

        close_spy.assert_called_once()
        assert bundle.app.code_db is None
        assert bundle.search_service.code_db is None
        assert bundle.index_service.code_db is None
        assert bundle.settings_dialog.code_db is None
        assert bundle.settings_dialog.storage_tab.code_db is None
        bundle.index_service.sync_model_state.assert_called_once()
        bundle.scheduler.wake.assert_called_once()
    finally:
        bundle.app.shutdown()


def test_trigger_force_reindex_resets_and_crawls(qapp: QApplication, tmp_path: Path) -> None:
    """Verify _trigger_force_reindex resets index records and runs general + code crawl."""
    del qapp
    bundle = _make_harness(tmp_path, code_enabled=True)
    try:
        bundle.app._trigger_force_reindex()
        assert bundle.app._crawl_thread is not None
        bundle.app._crawl_thread.join(timeout=2.0)

        bundle.index_service.reset_all_for_reindex.assert_called_once()
        bundle.index_service.crawl_and_sync.assert_any_call(is_code=False)
        bundle.index_service.crawl_and_sync.assert_any_call(is_code=True)
        assert bundle.index_service.crawl_and_sync.call_count == 2
    finally:
        bundle.app.shutdown()


def test_trigger_reindex_coalesces_pending_request_and_repeats_cycle(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    """Verify reindex requests arriving mid-crawl mark _reindex_pending and run one follow-up cycle."""
    del qapp
    bundle = _make_harness(tmp_path, code_enabled=False)
    entered_first = threading.Event()
    release_first = threading.Event()
    crawl_calls = 0

    def crawl_side_effect(*, is_code: bool = False) -> None:
        del is_code
        nonlocal crawl_calls
        crawl_calls += 1
        if crawl_calls == 1:
            entered_first.set()
            release_first.wait(timeout=2.0)

    bundle.index_service.crawl_and_sync.side_effect = crawl_side_effect

    try:
        bundle.app._trigger_reindex()
        t1 = bundle.app._crawl_thread
        assert t1 is not None
        assert entered_first.wait(timeout=2.0)

        # Second trigger while active must reuse the same thread and flag _reindex_pending.
        bundle.app._trigger_reindex()
        assert bundle.app._crawl_thread is t1
        pending_during_run = bundle.app._reindex_pending
        assert pending_during_run is True

        release_first.set()
        t1.join(timeout=2.0)
        assert not t1.is_alive()
        assert crawl_calls == 2
        assert bundle.app._reindex_pending is False

        # Subsequent trigger after completion spawns a fresh thread.
        bundle.app._trigger_reindex()
        t2 = bundle.app._crawl_thread
        assert t2 is not None
        assert t2 is not t1
        t2.join(timeout=2.0)
        assert crawl_calls == 3
    finally:
        bundle.app.shutdown()


def test_start_onboarding_cancelled_triggers_shutdown(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify rejecting OnboardingWizard immediately calls shutdown without leaving services running."""
    del qapp
    bundle = _make_harness(tmp_path, onboarded=False)

    fake_wizard = MagicMock()
    fake_wizard.indexing_started = MagicMock()
    fake_wizard.exec.return_value = QDialog.DialogCode.Rejected
    monkeypatch.setattr(app_module, "OnboardingWizard", MagicMock(return_value=fake_wizard))
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))

    bundle.app.start(daemon_mode=False)

    assert bundle.app.is_shutdown is True
    bundle.quit_spy.assert_called_once()
    bundle.hotkey_listener.start.assert_not_called()
    bundle.desktop_providers.register.assert_not_called()
    bundle.panel.show_panel.assert_not_called()


def test_start_no_tray_fallback_and_daemon_mode(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify start() shows panel when no tray is available unless daemon_mode=True."""
    del qapp
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))

    interactive = _make_harness(tmp_path / "interactive", onboarded=True)
    interactive_index_spy = MagicMock()
    monkeypatch.setattr(interactive.app, "_start_indexing", interactive_index_spy)
    try:
        interactive.app.start(daemon_mode=False)
        interactive_index_spy.assert_called_once()
        interactive.hotkey_listener.start.assert_called_once()
        interactive.settings_dialog.set_hotkey_status.assert_called_once_with("pynput", "")
        interactive.desktop_providers.register.assert_called_once()
        interactive.tray.show.assert_not_called()
        interactive.panel.show_panel.assert_called_once()
    finally:
        interactive.app.shutdown()

    daemon = _make_harness(tmp_path / "daemon", onboarded=True)
    daemon_index_spy = MagicMock()
    monkeypatch.setattr(daemon.app, "_start_indexing", daemon_index_spy)
    try:
        daemon.app.start(daemon_mode=True)
        daemon_index_spy.assert_called_once()
        daemon.desktop_providers.register.assert_called_once()
        daemon.panel.show_panel.assert_not_called()
    finally:
        daemon.app.shutdown()


def test_shutdown_closes_all_resources_and_is_idempotent(
    qapp: QApplication,
    tmp_path: Path,
    short_ipc_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify shutdown stops all services, joins crawl thread, closes DBs/Ollama/UI, and releases IPC."""
    del qapp, short_ipc_dir
    bundle = _make_harness(tmp_path, code_enabled=True, init_ipc=True)
    db_close_spy = MagicMock(wraps=bundle.db.close)
    monkeypatch.setattr(bundle.db, "close", db_close_spy)
    assert bundle.app.code_db is not None
    code_db_close_spy = MagicMock(wraps=bundle.app.code_db.close)
    monkeypatch.setattr(bundle.app.code_db, "close", code_db_close_spy)

    bg_thread = threading.Thread(target=lambda: time.sleep(0.05), daemon=True)
    bg_thread.start()
    monkeypatch.setattr(bundle.app, "_crawl_thread", bg_thread)

    bundle.app.shutdown()
    bundle.app.shutdown()  # Idempotent second call

    assert bundle.app.is_shutdown is True
    assert not bg_thread.is_alive()
    assert bundle.app._crawl_thread is None
    bundle.desktop_providers.unregister.assert_called_once()
    bundle.hotkey_listener.stop.assert_called_once()
    bundle.tray.stop.assert_called_once()
    bundle.scheduler.stop.assert_called_once()
    bundle.watcher.stop.assert_called_once()
    bundle.index_service.stop.assert_called_once()
    bundle.panel.shutdown.assert_called_once()
    bundle.settings_dialog.shutdown.assert_called_once()
    bundle.ollama.close.assert_called_once()
    db_close_spy.assert_called_once()
    code_db_close_spy.assert_called_once()
    assert bundle.app.ipc_server is None
    assert bundle.app._instance_lock_fd is None
    bundle.quit_spy.assert_called_once()


def test_cli_parser_flags_subcommands_and_localized_help(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify build_parser flags, mutual exclusion, subcommands, and localized --help via ConfigManager."""
    set_language("es")
    es_help = build_parser().format_help()
    assert "Motor de búsqueda semántica de escritorio para Linux" in es_help
    assert "Alternar el panel flotante de búsqueda rápida" in es_help

    set_language("en")
    parser = build_parser()
    assert parser.parse_args(["--toggle"]).toggle is True
    assert parser.parse_args(["--panel"]).panel is True
    assert parser.parse_args(["--settings"]).settings is True
    assert parser.parse_args(["--daemon"]).daemon is True

    with pytest.raises(SystemExit):
        parser.parse_args(["--panel", "--toggle"])

    q_args = parser.parse_args(["query", "budget", "forecast", "--code", "--kind", "pdf", "--limit", "5", "--json"])
    assert q_args.command == "query"
    assert q_args.query == ["budget", "forecast"]
    assert q_args.code is True
    assert q_args.kind == ["pdf"]
    assert q_args.limit == 5
    assert q_args.json is True

    # main(["--help"]) configures language from ConfigManager before building parser
    cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")
    cfg_mgr.config.language = "es"
    cfg_mgr.save()
    monkeypatch.setattr(main_module, "ConfigManager", lambda: cfg_mgr)

    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    assert "Motor de búsqueda semántica de escritorio para Linux" in capsys.readouterr().out


def test_main_dispatches_headless_subcommands_without_starting_gui(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify status, query, reindex, and install-desktop dispatch directly without creating SemSearchApp."""
    cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")
    cfg_mgr.config.language = "en"
    cfg_mgr.save()
    monkeypatch.setattr(main_module, "ConfigManager", lambda: cfg_mgr)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    app_ctor = MagicMock(side_effect=AssertionError("SemSearchApp must not be instantiated for CLI subcommands"))
    monkeypatch.setattr(main_module, "SemSearchApp", app_ctor)

    # Real status --json execution
    rc_status = main(["status", "--json"])
    assert rc_status == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert "main" in status_payload

    # Dispatch verification for query, reindex, and install-desktop
    query_spy = MagicMock(return_value=0)
    reindex_spy = MagicMock(return_value=0)
    install_spy = MagicMock(return_value=0)
    monkeypatch.setattr(main_module, "run_query", query_spy)
    monkeypatch.setattr(main_module, "run_reindex", reindex_spy)
    monkeypatch.setattr(main_module, "run_install_desktop", install_spy)

    assert main(["query", "quarterly", "budget", "--code", "--kind", "pdf", "--limit", "7", "--json"]) == 0
    query_spy.assert_called_once_with(
        "quarterly budget",
        is_code=True,
        kinds=["pdf"],
        limit=7,
        as_json=True,
    )

    assert main(["reindex", "--force"]) == 0
    reindex_spy.assert_called_once_with(force=True, forward_ipc=forward_ipc_command)

    target_dir = tmp_path / "desktop_data"
    assert main(["install-desktop", "--data-home", str(target_dir)]) == 0
    install_spy.assert_called_once_with(data_home=target_dir)
    app_ctor.assert_not_called()


def test_main_gui_flags_ipc_forwarding_and_cold_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify main() forwards GUI actions over IPC or cold-starts SemSearchApp cleanly."""
    cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")
    cfg_mgr.save()
    monkeypatch.setattr(main_module, "ConfigManager", lambda: cfg_mgr)

    # 1. Existing instance running: forward_ipc_command returns True and SemSearchApp is not created
    ipc_spy = MagicMock(return_value=True)
    app_ctor = MagicMock()
    monkeypatch.setattr(main_module, "forward_ipc_command", ipc_spy)
    monkeypatch.setattr(main_module, "SemSearchApp", app_ctor)

    assert main(["--toggle"]) == 0
    ipc_spy.assert_called_once_with("TOGGLE")
    app_ctor.assert_not_called()

    # 2. Cold start with --toggle opens panel via PANEL action
    monkeypatch.setattr(main_module, "forward_ipc_command", MagicMock(return_value=False))
    fake_app = MagicMock()
    fake_app.is_shutdown = False
    fake_app.exec.return_value = 0
    monkeypatch.setattr(main_module, "SemSearchApp", MagicMock(return_value=fake_app))

    assert main(["--toggle"]) == 0
    fake_app.start.assert_called_once_with(daemon_mode=False)
    fake_app.handle_cli_action.assert_called_once_with("PANEL")
    fake_app.exec.assert_called_once()

    # 3. Cold start with --panel routes PANEL action
    fake_panel = MagicMock()
    fake_panel.is_shutdown = False
    fake_panel.exec.return_value = 0
    monkeypatch.setattr(main_module, "SemSearchApp", MagicMock(return_value=fake_panel))

    assert main(["--panel"]) == 0
    fake_panel.start.assert_called_once_with(daemon_mode=False)
    fake_panel.handle_cli_action.assert_called_once_with("PANEL")
    fake_panel.exec.assert_called_once()

    # 4. Cold start with --daemon does not trigger an extra UI action
    fake_daemon = MagicMock()
    fake_daemon.is_shutdown = False
    fake_daemon.exec.return_value = 0
    monkeypatch.setattr(main_module, "SemSearchApp", MagicMock(return_value=fake_daemon))

    assert main(["--daemon"]) == 0
    fake_daemon.start.assert_called_once_with(daemon_mode=True)
    fake_daemon.handle_cli_action.assert_not_called()
    fake_daemon.exec.assert_called_once()

    # 5. Cancelled onboarding exits cleanly without entering app.exec()
    cancelled_app = MagicMock()
    cancelled_app.is_shutdown = True
    monkeypatch.setattr(main_module, "SemSearchApp", MagicMock(return_value=cancelled_app))

    assert main(["--settings"]) == 0
    cancelled_app.start.assert_called_once_with(daemon_mode=False)
    cancelled_app.handle_cli_action.assert_not_called()
    cancelled_app.exec.assert_not_called()
