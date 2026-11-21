"""Application lifecycle manager, single-instance IPC server, and widget coordination."""

import contextlib
import fcntl
import functools
import os
import socket
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QDialog, QSystemTrayIcon

from semsearch.cli import get_default_data_dir
from semsearch.config import ConfigManager
from semsearch.core.database import CODE_DB_FILE, MAIN_DB_FILE, Database
from semsearch.core.ollama import OllamaClient
from semsearch.gui.desktop_providers import DesktopSearchProviders
from semsearch.gui.hotkey import HotkeyListener
from semsearch.gui.onboarding import OnboardingWizard
from semsearch.gui.panel import SearchPanel
from semsearch.gui.settings import SettingsDialog
from semsearch.gui.theme import apply_theme
from semsearch.gui.tray import SystemTrayManager
from semsearch.i18n import is_rtl, set_language
from semsearch.services.indexer import IndexService
from semsearch.services.scheduler import PowerScheduler
from semsearch.services.searcher import SearchService
from semsearch.services.watcher import FileSystemWatcher

_MAX_IPC_MESSAGE_BYTES = 4096
_APP_ICON_PATH = Path(__file__).parent / "icons" / "semsearch.svg"


def get_ipc_runtime_dir() -> Path:
    """Return owner-private runtime directory for SemSearch IPC socket and lockfile."""
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    xdg_path = Path(xdg_runtime) if xdg_runtime else None
    runtime_dir = (
        xdg_path / "semsearch"
        if xdg_path is not None and xdg_path.is_dir()
        else Path(tempfile.gettempdir()) / f"semsearch_{os.getuid()}"
    )
    runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    runtime_dir.chmod(0o700)
    return runtime_dir


def get_ipc_server_name() -> str:
    """Return filesystem path of the local IPC socket for the current user session."""
    return str(get_ipc_runtime_dir() / "ipc.sock")


def forward_ipc_command(command: str, timeout: float = 1.0) -> bool:
    """Forward a command string to a running SemSearch instance over AF_UNIX socket."""
    server_name = get_ipc_server_name()
    sock_path = server_name if server_name.startswith("/") else str(Path(tempfile.gettempdir()) / server_name)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(sock_path)
            client.sendall(command.encode("utf-8"))
    except OSError:
        return False
    return True


class SemSearchApp(QApplication):
    """Main application shell coordinating single-instance lock, services, and GUI."""

    def __init__(
        self,
        argv: list[str],
        config_manager: ConfigManager | None = None,
        db: Database | None = None,
        code_db: Database | None = None,
        ollama_client: OllamaClient | None = None,
    ) -> None:
        super().__init__(argv)
        self.setApplicationName("SemSearch")
        self.setDesktopFileName("semsearch")
        self.setWindowIcon(QIcon(str(_APP_ICON_PATH)))
        self.setQuitOnLastWindowClosed(False)
        apply_theme(self)

        self.config_manager = config_manager or ConfigManager()
        self.config = self.config_manager.config
        self._apply_language()

        data_dir = get_default_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db = db or Database(data_dir / MAIN_DB_FILE)
        self.code_db = (code_db or Database(data_dir / CODE_DB_FILE)) if self.config.code_enabled else None

        self.ollama = ollama_client or OllamaClient(
            base_url=self.config.ollama_url,
            num_ctx=self.config.num_ctx,
        )
        self.search_service = SearchService(
            self.db,
            code_db=self.code_db,
            ollama=self.ollama,
            model=self.config.model,
        )
        self.index_service = IndexService(self.db, code_db=self.code_db, ollama=self.ollama, config=self.config)
        self.watcher = FileSystemWatcher(self.index_service, self.config)
        self.scheduler = PowerScheduler(self.index_service, self.config)

        # Single-instance IPC server and lockfile state
        self.ipc_server: QLocalServer | None = None
        self._instance_lock_fd: int | None = None
        self._ipc_server_name = ""
        self._crawl_thread: threading.Thread | None = None
        self._crawl_lock = threading.Lock()
        self._crawl_active = False
        self._reindex_pending = False
        self.is_shutdown = False
        self._init_ipc_server()

        # UI components and desktop integrations
        self.tray = SystemTrayManager(self.index_service)
        self.panel = SearchPanel(self.search_service, self.config)
        self.settings_dialog = SettingsDialog(self.config_manager, self.ollama, self.db, code_db=self.code_db)
        self.desktop_providers = DesktopSearchProviders(self.search_service, self.config, parent=self)
        self.hotkey_listener = HotkeyListener(self.config.hotkey, parent=self)

        self._wire_signals()

    def _apply_language(self) -> None:
        resolved = set_language(self.config.language)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft if is_rtl(resolved) else Qt.LayoutDirection.LeftToRight)

    def _init_ipc_server(self) -> None:
        runtime_dir = get_ipc_runtime_dir()
        lock_path = runtime_dir / "instance.lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return

        os.fchmod(fd, 0o600)
        self._instance_lock_fd = fd

        server_name = get_ipc_server_name()
        QLocalServer.removeServer(server_name)
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        server.newConnection.connect(self._on_ipc_connection)
        if not server.listen(server_name):
            server.close()
            self._release_instance_lock()
            return

        sock_path = Path(server_name)
        if sock_path.is_socket():
            sock_path.chmod(0o600)
        self.ipc_server = server
        self._ipc_server_name = server_name

    def _release_instance_lock(self) -> None:
        if self._instance_lock_fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self._instance_lock_fd, fcntl.LOCK_UN)
            os.close(self._instance_lock_fd)
            self._instance_lock_fd = None

    def _close_ipc_server(self) -> None:
        if self.ipc_server is not None:
            self.ipc_server.close()
            self.ipc_server = None
            if self._instance_lock_fd is not None and self._ipc_server_name:
                QLocalServer.removeServer(self._ipc_server_name)
        self._ipc_server_name = ""
        self._release_instance_lock()

    def _on_ipc_connection(self) -> None:
        if self.ipc_server is None:
            return
        while self.ipc_server.hasPendingConnections():
            client = self.ipc_server.nextPendingConnection()
            client.disconnected.connect(client.deleteLater)
            if client.bytesAvailable() > 0:
                self._handle_ipc_message(client)
            else:
                client.readyRead.connect(functools.partial(self._handle_ipc_message, client))

    def _handle_ipc_message(self, client: QLocalSocket) -> None:
        msg = bytes(client.read(_MAX_IPC_MESSAGE_BYTES).data()).decode("utf-8", errors="ignore").strip()
        client.disconnectFromServer()
        client.close()
        client.deleteLater()
        if msg:
            self.handle_cli_action(msg)

    def _wire_signals(self) -> None:
        self.tray.open_panel_requested.connect(self.show_panel)
        self.tray.open_settings_requested.connect(self.show_settings)
        self.tray.quit_requested.connect(self.shutdown)

        self.settings_dialog.reindex_requested.connect(self._trigger_reindex)
        self.settings_dialog.force_reindex_requested.connect(self._trigger_force_reindex)
        self.settings_dialog.settings_saved.connect(self._on_settings_saved)

        self.desktop_providers.launch_panel_requested.connect(self._open_panel_with_query)
        self.hotkey_listener.hotkey_triggered.connect(self.toggle_panel)

    def _set_code_db(self, code_db: Database | None) -> None:
        self.code_db = code_db
        self.search_service.code_db = code_db
        self.index_service.code_db = code_db
        self.settings_dialog.code_db = code_db
        self.settings_dialog.storage_tab.code_db = code_db

    def _sync_code_db(self) -> None:
        if self.config.code_enabled and self.code_db is None:
            data_dir = get_default_data_dir()
            data_dir.mkdir(parents=True, exist_ok=True)
            self._set_code_db(Database(data_dir / CODE_DB_FILE))
        elif not self.config.code_enabled and self.code_db is not None:
            old_code_db = self.code_db
            self._set_code_db(None)
            old_code_db.close()

    def _sync_hotkey_status(self, _backend: str = "") -> None:
        self.settings_dialog.set_hotkey_status(
            self.hotkey_listener.backend_name,
            self.hotkey_listener.last_error,
        )

    def _start_hotkey_listener(self) -> None:
        self.hotkey_listener.start()
        self._sync_hotkey_status()
        self.hotkey_listener.status_changed.connect(self._sync_hotkey_status)

    def _on_settings_saved(self) -> None:
        """Apply updated settings to active services, watchers, hotkey listener, and index."""
        self.config = self.config_manager.config
        self._apply_language()
        self.ollama.base_url = self.config.ollama_url
        self.ollama.num_ctx = self.config.num_ctx
        self.search_service.model = self.config.model

        self.watcher.stop()
        self._sync_code_db()

        self.tray.retranslate_ui()
        self.panel.retranslate_ui()
        self.settings_dialog.retranslate_ui()

        self.index_service.sync_model_state()
        self.scheduler.wake()
        self.watcher.start()

        old_hotkey = self.hotkey_listener
        old_hotkey.stop()
        old_hotkey.deleteLater()
        self.hotkey_listener = HotkeyListener(self.config.hotkey, parent=self)
        self.hotkey_listener.hotkey_triggered.connect(self.toggle_panel)
        self._start_hotkey_listener()

        self._trigger_reindex()

    def _open_panel_with_query(self, query: str) -> None:
        self.show_panel()
        if query:
            self.panel.execute_search(query)

    def handle_cli_action(self, action: str) -> None:
        """Route CLI or IPC action string."""
        cmd = action.strip().upper().replace("--", "")
        if cmd == "TOGGLE":
            self.toggle_panel()
        elif cmd in ("PANEL", "SHOW_PANEL"):
            self.show_panel()
        elif cmd in ("SETTINGS", "SHOW_SETTINGS"):
            self.show_settings()
        elif cmd == "REINDEX":
            self._trigger_reindex()
        elif cmd == "REINDEX_FORCE":
            self._trigger_force_reindex()
        elif cmd in ("DAEMON", "") and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def toggle_panel(self) -> None:
        """Toggle quick search panel visibility."""
        if self.panel.isVisible():
            self.panel.hide_panel()
        else:
            self.show_panel()

    def show_panel(self) -> None:
        """Bring quick search panel to foreground."""
        self.panel.show_panel()

    def show_settings(self) -> None:
        """Open settings dialog."""
        self.settings_dialog.load_settings()
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def start(self, *, daemon_mode: bool = False) -> None:
        """Boot application services, run onboarding if needed, and register desktop integrations."""
        tray_available = QSystemTrayIcon.isSystemTrayAvailable()
        if tray_available:
            self.tray.show()

        if not self.config.onboarded:
            wizard = OnboardingWizard(self.config_manager, self.ollama)
            wizard.indexing_started.connect(self._on_onboarding_finished)
            if wizard.exec() != QDialog.DialogCode.Accepted:
                self.shutdown()
                return
        else:
            self._start_indexing()

        self._start_hotkey_listener()
        self.desktop_providers.register()

        if not tray_available and not daemon_mode and self.config.onboarded:
            self.show_panel()

    def _on_onboarding_finished(self) -> None:
        self.search_service.model = self.config.model
        self._sync_code_db()
        self._start_indexing()

    def _start_indexing(self) -> None:
        self.index_service.start()
        self.watcher.start()
        self.scheduler.start()
        self._trigger_reindex()

    def _trigger_force_reindex(self) -> None:
        self.index_service.reset_all_for_reindex()
        self._trigger_reindex()

    def _trigger_reindex(self) -> None:
        with self._crawl_lock:
            if self._crawl_active:
                self._reindex_pending = True
                return
            self._crawl_active = True
            self._reindex_pending = False
            self._crawl_thread = threading.Thread(target=self._crawl_loop, daemon=True)
            self._crawl_thread.start()

    def _crawl_loop(self) -> None:
        try:
            while True:
                with self._crawl_lock:
                    self._reindex_pending = False
                self._initial_crawl()
                with self._crawl_lock:
                    if not self._reindex_pending:
                        self._crawl_active = False
                        return
        except BaseException:
            with self._crawl_lock:
                self._crawl_active = False
            raise

    def _initial_crawl(self) -> None:
        self.index_service.crawl_and_sync(is_code=False)
        if self.config.code_enabled:
            self.index_service.crawl_and_sync(is_code=True)

    def shutdown(self) -> None:
        """Gracefully terminate background services, close resources, and exit."""
        if self.is_shutdown:
            return
        self.is_shutdown = True
        with self._crawl_lock:
            self._reindex_pending = False

        self.desktop_providers.unregister()
        self.hotkey_listener.stop()
        self.tray.stop()
        self.scheduler.stop()
        self.watcher.stop()
        self.index_service.stop()
        if self._crawl_thread is not None and self._crawl_thread.is_alive():
            self._crawl_thread.join(timeout=2.0)
        self._crawl_thread = None
        self._crawl_active = False

        self.panel.shutdown()
        self.settings_dialog.shutdown()
        self.ollama.close()
        self.db.close()
        if self.code_db is not None:
            self.code_db.close()
        self._close_ipc_server()
        self.quit()
