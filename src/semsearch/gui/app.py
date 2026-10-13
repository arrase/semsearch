"""Application lifecycle manager, single-instance IPC server, and widget coordination."""

import os
from pathlib import Path
import threading
from PySide6.QtCore import Qt
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from semsearch.config import ConfigManager
from semsearch.core.database import CODE_DB_FILE, MAIN_DB_FILE, Database
from semsearch.core.ollama import OllamaClient
from semsearch.gui.hotkey import HotkeyListener
from semsearch.gui.onboarding import OnboardingWizard
from semsearch.gui.panel import SearchPanel
from semsearch.gui.settings import SettingsDialog
from semsearch.gui.tray import SystemTrayManager
from semsearch.gui.window import SearchWindow
from semsearch.i18n import is_rtl, set_language
from semsearch.services.indexer import IndexService
from semsearch.services.scheduler import PowerScheduler
from semsearch.services.searcher import SearchService
from semsearch.services.watcher import FileSystemWatcher


def get_ipc_server_name() -> str:
    """Return unique local socket name for current user session."""
    return f"semsearch_ipc_{os.getuid()}"


def forward_ipc_command(command: str) -> bool:
    """Attempt forwarding command to an existing running SemSearch instance."""
    server_name = get_ipc_server_name()
    socket = QLocalSocket()
    socket.connectToServer(server_name)
    if socket.waitForConnected(200):
        socket.write(command.encode("utf-8"))
        socket.flush()
        socket.waitForBytesWritten(200)
        socket.disconnectFromServer()
        return True
    return False


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
        self.setQuitOnLastWindowClosed(False)

        self.config_manager = config_manager or ConfigManager()
        self.config = self.config_manager.config
        self._apply_language()

        data_dir = Path.home() / ".local" / "share" / "semsearch"
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

        # IPC Server for single instance enforcement
        self.ipc_server: QLocalServer | None = None
        self._crawl_thread: threading.Thread | None = None
        self._init_ipc_server()

        # UI Components
        self.tray = SystemTrayManager(self.index_service)
        self.panel = SearchPanel(self.search_service, self.config)
        self.window = SearchWindow(self.search_service, self.config)
        self.settings_dialog = SettingsDialog(
            self.config_manager, self.ollama, self.db, code_db=self.code_db
        )

        # Hotkey listener
        self.hotkey_listener = HotkeyListener(self.config.hotkey)
        self.hotkey_listener.hotkey_triggered.connect(self.toggle_panel)

        self._wire_signals()

    def _apply_language(self) -> None:
        resolved = set_language(self.config.language)
        self.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if is_rtl(resolved) else Qt.LayoutDirection.LeftToRight
        )

    def _init_ipc_server(self) -> None:
        server_name = get_ipc_server_name()
        QLocalServer.removeServer(server_name)
        self.ipc_server = QLocalServer(self)
        self.ipc_server.newConnection.connect(self._on_ipc_connection)
        self.ipc_server.listen(server_name)

    def _on_ipc_connection(self) -> None:
        if self.ipc_server is None:
            return
        client = self.ipc_server.nextPendingConnection()
        if client:
            client.readyRead.connect(lambda: self._handle_ipc_message(client))
            if client.bytesAvailable() > 0:
                self._handle_ipc_message(client)

    def _handle_ipc_message(self, client: QLocalSocket) -> None:
        msg = client.readAll().data().decode("utf-8").strip()
        if msg:
            self.handle_cli_action(msg)

    def _wire_signals(self) -> None:
        self.tray.open_panel_requested.connect(self.show_panel)
        self.tray.open_window_requested.connect(self.show_window)
        self.tray.open_settings_requested.connect(self.show_settings)
        self.tray.quit_requested.connect(self.shutdown)

        self.panel.open_window_requested.connect(self.show_window)
        self.window.settings_requested.connect(self.show_settings)
        self.settings_dialog.reindex_requested.connect(self._trigger_reindex)
        self.settings_dialog.settings_saved.connect(self._on_settings_saved)

    def _on_settings_saved(self) -> None:
        """Apply updated settings to active services, watchers, hotkey listener, and index."""
        self.config = self.config_manager.config
        self._apply_language()
        self.tray.retranslate_ui()
        self.panel.retranslate_ui()
        self.window.retranslate_ui()
        self.settings_dialog.retranslate_ui()

        self.ollama.base_url = self.config.ollama_url
        self.ollama.num_ctx = self.config.num_ctx
        self.search_service.model = self.config.model

        if self.config.code_enabled and self.code_db is None:
            data_dir = Path.home() / ".local" / "share" / "semsearch"
            data_dir.mkdir(parents=True, exist_ok=True)
            self.code_db = Database(data_dir / CODE_DB_FILE)
            self.search_service.code_db = self.code_db
            self.index_service.code_db = self.code_db
            self.settings_dialog.code_db = self.code_db
            self.settings_dialog.storage_tab.code_db = self.code_db

        self.watcher.stop()
        self.watcher.start()

        self.hotkey_listener.stop()
        self.hotkey_listener = HotkeyListener(self.config.hotkey)
        self.hotkey_listener.hotkey_triggered.connect(self.toggle_panel)
        try:
            self.hotkey_listener.start()
        except Exception:
            pass

        self._trigger_reindex()

    def handle_cli_action(self, action: str) -> None:
        """Route CLI or IPC action string."""
        cmd = action.upper().replace("--", "")
        if cmd in ("PANEL", "SHOW_PANEL"):
            self.show_panel()
        elif cmd in ("WINDOW", "SHOW_WINDOW"):
            self.show_window()
        elif cmd in ("SETTINGS", "SHOW_SETTINGS"):
            self.show_settings()
        elif cmd in ("DAEMON", ""):
            self.tray.show()

    def toggle_panel(self) -> None:
        """Toggle quick search panel visibility."""
        if self.panel.isVisible() and self.panel.isActiveWindow():
            self.panel.hide_panel()
        else:
            self.show_panel()

    def show_panel(self) -> None:
        """Bring quick search panel to foreground."""
        self.panel.show_panel()

    def show_window(self) -> None:
        """Bring full search window to foreground."""
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def show_settings(self) -> None:
        """Open settings dialog."""
        self.settings_dialog.load_settings()
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def start(self) -> None:
        """Boot application services and check onboarding status."""
        self.tray.show()

        if not self.config.onboarded:
            wizard = OnboardingWizard(self.config_manager, self.ollama)
            wizard.indexing_started.connect(self._start_indexing)
            wizard.exec()
        else:
            self._start_indexing()

        try:
            self.hotkey_listener.start()
        except Exception:
            pass

    def _start_indexing(self) -> None:
        self.index_service.start()
        self.watcher.start()
        self.scheduler.start()
        self._trigger_reindex()

    def _trigger_reindex(self) -> None:
        if self._crawl_thread is None or not self._crawl_thread.is_alive():
            self._crawl_thread = threading.Thread(target=self._initial_crawl, daemon=True)
            self._crawl_thread.start()

    def _initial_crawl(self) -> None:
        self.index_service.crawl_and_sync(is_code=False)
        if self.config.code_enabled:
            self.index_service.crawl_and_sync(is_code=True)

    def shutdown(self) -> None:
        """Gracefully terminate background services and exit."""
        self.hotkey_listener.stop()
        self.scheduler.stop()
        self.watcher.stop()
        self.index_service.stop()
        if self._crawl_thread is not None and self._crawl_thread.is_alive():
            self._crawl_thread.join(timeout=2.0)
        self._crawl_thread = None
        if self.ipc_server:
            self.ipc_server.close()
            QLocalServer.removeServer(get_ipc_server_name())
        self.quit()
