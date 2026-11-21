"""Settings dialog with four configuration tabs and verified multimodal model filtering."""

import shutil
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTime, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from semsearch.config import ConfigManager
from semsearch.core.database import Database
from semsearch.core.ollama import OllamaClient
from semsearch.gui.hotkey import normalize_hotkey
from semsearch.gui.preview import format_file_size
from semsearch.gui.theme import ThemeColors, apply_theme, get_ui_font
from semsearch.gui.widgets import DEFAULT_MODEL, FolderListEditor, OllamaModelPicker, qt_escape_amp
from semsearch.i18n import SUPPORTED_LANGUAGES, tr

AUTOSTART_DIR = Path.home() / ".config" / "autostart"
AUTOSTART_FILE = AUTOSTART_DIR / "semsearch.desktop"

EDITOR_OPTIONS = [
    ("Auto-detect", "auto"),
    ("VS Code", "code"),
    ("Cursor", "cursor"),
    ("Zed", "zed"),
    ("Sublime Text", "subl"),
    ("JetBrains", "jetbrains"),
]


def is_autostart_enabled() -> bool:
    """Check if autostart desktop entry exists."""
    return AUTOSTART_FILE.is_file()


def set_autostart_enabled(*, enabled: bool) -> None:
    """Create or remove autostart desktop entry."""
    if enabled:
        AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
        local_bin = Path.home() / ".local" / "bin" / "semsearch"
        exec_path = shutil.which("semsearch") or (str(local_bin) if local_bin.is_file() else "semsearch")
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=SemSearch\n"
            "Comment=Linux desktop semantic search engine\n"
            f'Exec="{exec_path}" --daemon\n'
            "Icon=semsearch\n"
            "Hidden=false\n"
            "NoDisplay=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        AUTOSTART_FILE.write_text(content, encoding="utf-8")
    elif AUTOSTART_FILE.is_file():
        AUTOSTART_FILE.unlink()


class _VacuumWorker(QThread):
    """Background worker executing SQLite VACUUM off the GUI event loop."""

    vacuum_completed = Signal()

    def __init__(self, databases: list[Database], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._databases = databases

    def run(self) -> None:
        for database in self._databases:
            database.vacuum()
        self.vacuum_completed.emit()


class FoldersTab(QWidget):
    """Tab 1: Search folders, exclusions, power policy, and operating schedule."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        self.folders_group = self._build_folders_group()
        self.excl_group = self._build_exclusions_group()
        self.power_group = self._build_power_group()

        layout.addWidget(self.folders_group, stretch=1)
        layout.addWidget(self.excl_group)
        layout.addWidget(self.power_group)

    def _build_folders_group(self) -> QGroupBox:
        group = QGroupBox(tr("Indexed Folders"))
        folders_layout = QVBoxLayout(group)
        self.folders_editor = FolderListEditor(
            tr("Add Folder..."),
            tr("Remove"),
            tr("Select Folder to Index"),
        )
        self.folders_list = self.folders_editor.list_widget
        self.folders_list.setMinimumHeight(110)
        self.add_folder_btn = self.folders_editor.add_btn
        self.remove_folder_btn = self.folders_editor.remove_btn
        folders_layout.addWidget(self.folders_editor, stretch=1)
        return group

    def _build_exclusions_group(self) -> QGroupBox:
        group = QGroupBox(tr("Exclusions"))
        excl_layout = QFormLayout(group)
        excl_layout.setSpacing(8)
        self.skipped_folders_edit = QLineEdit()
        self.skipped_folders_edit.setPlaceholderText(
            tr("comma-separated folder names e.g. node_modules, .venv, target")
        )
        self.skipped_ext_edit = QLineEdit()
        self.skipped_ext_edit.setPlaceholderText(tr("comma-separated extensions e.g. heic, log"))
        self.skipped_folders_label = QLabel(tr("Skipped Folders:"))
        self.skipped_ext_label = QLabel(tr("Skipped Extensions:"))
        excl_layout.addRow(self.skipped_folders_label, self.skipped_folders_edit)
        excl_layout.addRow(self.skipped_ext_label, self.skipped_ext_edit)
        return group

    def _build_power_group(self) -> QGroupBox:
        group = QGroupBox(qt_escape_amp(tr("Power && Indexing Schedule")))
        power_layout = QVBoxLayout(group)
        power_layout.setSpacing(8)
        self.battery_check = QCheckBox(tr("Index while on battery power"))

        schedule_row = QHBoxLayout()
        schedule_row.setSpacing(8)
        self.schedule_check = QCheckBox(tr("Limit indexing to hours:"))
        self.start_time_edit = QTimeEdit()
        self.start_time_edit.setDisplayFormat("HH:mm")
        self.start_time_edit.setMinimumWidth(90)
        self.end_time_edit = QTimeEdit()
        self.end_time_edit.setDisplayFormat("HH:mm")
        self.end_time_edit.setMinimumWidth(90)
        self.to_label = QLabel(tr("to"))

        schedule_row.addWidget(self.schedule_check)
        schedule_row.addWidget(self.start_time_edit)
        schedule_row.addWidget(self.to_label)
        schedule_row.addWidget(self.end_time_edit)
        schedule_row.addStretch()

        power_layout.addWidget(self.battery_check)
        power_layout.addLayout(schedule_row)
        return group

    def retranslate_ui(self) -> None:
        """Refresh translated labels in FoldersTab."""
        self.folders_group.setTitle(tr("Indexed Folders"))
        self.folders_editor.retranslate_ui(
            tr("Add Folder..."),
            tr("Remove"),
            tr("Select Folder to Index"),
        )
        self.excl_group.setTitle(tr("Exclusions"))
        self.skipped_folders_edit.setPlaceholderText(
            tr("comma-separated folder names e.g. node_modules, .venv, target")
        )
        self.skipped_ext_edit.setPlaceholderText(tr("comma-separated extensions e.g. heic, log"))
        self.skipped_folders_label.setText(tr("Skipped Folders:"))
        self.skipped_ext_label.setText(tr("Skipped Extensions:"))
        self.power_group.setTitle(qt_escape_amp(tr("Power && Indexing Schedule")))
        self.battery_check.setText(tr("Index while on battery power"))
        self.schedule_check.setText(tr("Limit indexing to hours:"))
        self.to_label.setText(tr("to"))


class CodeTab(QWidget):
    """Tab 2: Code search toggle, code root directories, and editor picker."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        self.code_enable_check = QCheckBox(tr("Enable Source Code Search"))
        self.code_enable_check.setFont(get_ui_font(13))
        layout.addWidget(self.code_enable_check)

        self.code_folders_group = QGroupBox(tr("Code Root Directories"))
        code_layout = QVBoxLayout(self.code_folders_group)
        self.code_folders_editor = FolderListEditor(
            tr("Add Code Root..."),
            tr("Remove"),
            tr("Select Code Root Directory"),
        )
        self.code_folders_list = self.code_folders_editor.list_widget
        self.add_code_btn = self.code_folders_editor.add_btn
        self.remove_code_btn = self.code_folders_editor.remove_btn
        code_layout.addWidget(self.code_folders_editor)
        layout.addWidget(self.code_folders_group)

        self.editor_group = QGroupBox(tr("Preferred Code Editor"))
        editor_layout = QHBoxLayout(self.editor_group)
        self.editor_combo = QComboBox()
        for label, val in EDITOR_OPTIONS:
            self.editor_combo.addItem(tr(label) if val == "auto" else label, val)
        self.open_with_label = QLabel(tr("Open code results with:"))
        editor_layout.addWidget(self.open_with_label)
        editor_layout.addWidget(self.editor_combo, stretch=1)
        layout.addWidget(self.editor_group)
        layout.addStretch()

    def retranslate_ui(self) -> None:
        """Refresh translated labels in CodeTab."""
        self.code_enable_check.setText(tr("Enable Source Code Search"))
        self.code_folders_group.setTitle(tr("Code Root Directories"))
        self.code_folders_editor.retranslate_ui(
            tr("Add Code Root..."),
            tr("Remove"),
            tr("Select Code Root Directory"),
        )
        self.editor_group.setTitle(tr("Preferred Code Editor"))
        self.open_with_label.setText(tr("Open code results with:"))
        auto_idx = self.editor_combo.findData("auto")
        if auto_idx >= 0:
            self.editor_combo.setItemText(auto_idx, tr("Auto-detect"))


class StorageModelTab(QWidget):
    """Tab 3: Multimodal embedding model configuration and local database maintenance."""

    reindex_requested = Signal()
    force_reindex_requested = Signal()

    def __init__(
        self,
        ollama_client: OllamaClient,
        db: Database | None = None,
        code_db: Database | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.ollama = ollama_client
        self.db = db
        self.code_db = code_db
        self._vacuum_worker: _VacuumWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        self.ollama_group = QGroupBox(tr("Multimodal Embedding Model (Ollama)"))
        ollama_group_layout = QVBoxLayout(self.ollama_group)
        self.ollama_picker = OllamaModelPicker()
        self.ollama_picker.connection_succeeded.connect(self._on_connection_succeeded)

        self.url_edit = self.ollama_picker.url_edit
        self.test_btn = self.ollama_picker.test_btn
        self.model_combo = self.ollama_picker.model_combo
        self.status_label = self.ollama_picker.status_label
        self.host_url_label = self.ollama_picker.host_url_label
        self.model_label = self.ollama_picker.model_label

        self.num_ctx_label = QLabel(tr("Context Window (num_ctx):"))
        self.num_ctx_edit = QLineEdit()
        self.num_ctx_edit.setPlaceholderText(tr("0 (Model default)"))
        self.ollama_picker.insert_row(self.num_ctx_label, self.num_ctx_edit)

        ollama_group_layout.addWidget(self.ollama_picker)
        layout.addWidget(self.ollama_group)

        self.db_group = QGroupBox(qt_escape_amp(tr("Database && Storage Usage")))
        db_layout = QVBoxLayout(self.db_group)
        self.stats_label = QLabel(tr("Loading database metrics..."))
        self.stats_label.setFont(get_ui_font(12))
        self.stats_label.setWordWrap(True)

        db_btn_row = QHBoxLayout()
        self.vacuum_btn = QPushButton(tr("Optimize / Vacuum Database"))
        self.vacuum_btn.clicked.connect(self._vacuum_database)
        self.reindex_btn = QPushButton(tr("Re-index All Files"))
        self.reindex_btn.clicked.connect(self._on_reindex_clicked)
        db_btn_row.addWidget(self.vacuum_btn)
        db_btn_row.addWidget(self.reindex_btn)
        db_btn_row.addStretch()

        db_layout.addWidget(self.stats_label)
        db_layout.addLayout(db_btn_row)
        layout.addWidget(self.db_group)
        layout.addStretch()

    def retranslate_ui(self) -> None:
        """Refresh translated labels in StorageModelTab."""
        self.ollama_group.setTitle(tr("Multimodal Embedding Model (Ollama)"))
        self.ollama_picker.retranslate_ui()
        self.num_ctx_label.setText(tr("Context Window (num_ctx):"))
        self.num_ctx_edit.setPlaceholderText(tr("0 (Model default)"))
        self.db_group.setTitle(qt_escape_amp(tr("Database && Storage Usage")))
        self.vacuum_btn.setText(tr("Optimize / Vacuum Database"))
        self.reindex_btn.setText(tr("Re-index All Files"))
        self.update_database_stats()

    def _on_reindex_clicked(self) -> None:
        self.force_reindex_requested.emit()
        self.reindex_requested.emit()

    def _on_connection_succeeded(self, _models: list[str]) -> None:
        self.validate_num_ctx()

    def validate_num_ctx(self) -> int | None:
        """Validate user-entered context window size against a temporary OllamaClient probe."""
        raw = self.num_ctx_edit.text().strip()
        if not raw:
            return 0
        if not raw.isdigit():
            self.status_label.setText(tr("⚠ Invalid context window: enter a positive integer or 0 for default."))
            self.status_label.setStyleSheet(f"color: {ThemeColors.AMBER};")
            return None

        value = int(raw)
        if value == 0:
            return 0

        selected_model = self.ollama_picker.selected_model() or DEFAULT_MODEL
        with OllamaClient(base_url=self.ollama_picker.url()) as probe:
            max_ctx = probe.get_model_context_length(selected_model)
        if isinstance(max_ctx, int) and max_ctx > 0 and value > max_ctx:
            self.status_label.setText(
                tr(
                    "⚠ Context window ({num_ctx}) exceeds model maximum ({max_ctx}).",
                    num_ctx=value,
                    max_ctx=max_ctx,
                )
            )
            self.status_label.setStyleSheet(f"color: {ThemeColors.AMBER};")
            return None

        return value

    def test_connection(self) -> None:
        """Synchronously probe entered Ollama host without mutating the shared OllamaClient."""
        self.ollama_picker.test_connection()

    def populate_multimodal_models(self, models: list[str]) -> None:
        """Populate model dropdown with verified multimodal models, preserving current selection."""
        self.ollama_picker.populate_multimodal_models(models)

    def update_database_stats(self) -> None:
        """Update display of indexed files, chunks, and database disk usage."""
        if self.db is None and self.code_db is None:
            self.stats_label.setText(tr("Database metrics unavailable"))
            return

        total_files = 0
        total_segments = 0
        total_vectors = 0
        total_bytes = 0

        for db in (self.db, self.code_db):
            if db is not None:
                stats = db.stats()
                total_files += stats.get("files", 0)
                total_segments += stats.get("segments", 0)
                total_vectors += stats.get("vectors", 0)
                db_path = Path(db.path)
                if db_path.is_file():
                    total_bytes += db_path.stat().st_size

        disk_size = format_file_size(total_bytes)
        self.stats_label.setText(
            tr(
                "Indexed Files: {files}  |  Segments: {segments}  |  Vectors: {vectors}  |  Disk Size: {size}",
                files=f"{total_files:,}",
                segments=f"{total_segments:,}",
                vectors=f"{total_vectors:,}",
                size=disk_size,
            )
        )

    def _vacuum_database(self) -> None:
        if self._vacuum_worker is not None and self._vacuum_worker.isRunning():
            return
        databases = [db for db in (self.db, self.code_db) if db is not None]
        if not databases:
            self.update_database_stats()
            return
        self.vacuum_btn.setEnabled(False)
        worker = _VacuumWorker(databases, parent=self)
        self._vacuum_worker = worker
        worker.vacuum_completed.connect(self._on_vacuum_completed)
        worker.finished.connect(lambda w=worker: self._cleanup_vacuum_worker(w))
        worker.start()

    def _on_vacuum_completed(self) -> None:
        self.vacuum_btn.setEnabled(True)
        self.update_database_stats()

    def _cleanup_vacuum_worker(self, worker: _VacuumWorker) -> None:
        if self._vacuum_worker is worker:
            self._vacuum_worker = None
        worker.deleteLater()

    def shutdown(self) -> None:
        """Wait for background probe and vacuum threads before dialog teardown."""
        self.ollama_picker.shutdown()
        if self._vacuum_worker is not None:
            self._vacuum_worker.wait(5000)
            self._vacuum_worker = None
            self.vacuum_btn.setEnabled(True)


class GeneralTab(QWidget):
    """Tab 4: Global desktop shortcut, interface language, and system autostart."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        self.hotkey_group = QGroupBox(tr("Global Shortcut"))
        hotkey_layout = QFormLayout(self.hotkey_group)
        self.hotkey_edit = QLineEdit()
        self.hotkey_edit.setPlaceholderText("Ctrl+Shift+Space")
        self.hotkey_label = QLabel(tr("Quick Search Shortcut:"))
        self.status_label = QLabel("")
        self.status_label.setFont(get_ui_font(11))
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED};")
        self.hotkey_status_label = self.status_label
        hotkey_layout.addRow(self.hotkey_label, self.hotkey_edit)
        hotkey_layout.addRow("", self.status_label)
        layout.addWidget(self.hotkey_group)

        self.lang_group = QGroupBox(tr("Language"))
        lang_layout = QFormLayout(self.lang_group)
        self.language_combo = QComboBox()
        self.language_combo.addItem(tr("Auto-detect"), "auto")
        for code, name in SUPPORTED_LANGUAGES.items():
            self.language_combo.addItem(f"{name} ({code})", code)
        self.lang_label = QLabel(tr("Interface Language:"))
        lang_layout.addRow(self.lang_label, self.language_combo)
        layout.addWidget(self.lang_group)

        self.autostart_group = QGroupBox(tr("System Startup"))
        autostart_layout = QVBoxLayout(self.autostart_group)
        self.autostart_check = QCheckBox(tr("Start SemSearch automatically on desktop login"))
        autostart_layout.addWidget(self.autostart_check)
        layout.addWidget(self.autostart_group)
        layout.addStretch()

    def set_hotkey_status(self, backend_name: str, error: str = "") -> None:
        """Display active global shortcut backend (portal, pynput) or registration error."""
        if backend_name == "portal":
            self.status_label.setText("Active (Wayland XDG GlobalShortcuts portal)")
            self.status_label.setStyleSheet(f"color: {ThemeColors.LEAF};")
        elif backend_name == "pynput":
            self.status_label.setText("Active (X11 pynput)")
            self.status_label.setStyleSheet(f"color: {ThemeColors.LEAF};")
        elif error:
            self.status_label.setText(f"⚠ Global shortcut unavailable: {error}")
            self.status_label.setStyleSheet(f"color: {ThemeColors.AMBER};")
        else:
            self.status_label.setText("")
            self.status_label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED};")

    def retranslate_ui(self) -> None:
        """Refresh translated labels in GeneralTab."""
        self.hotkey_group.setTitle(tr("Global Shortcut"))
        self.hotkey_label.setText(tr("Quick Search Shortcut:"))
        self.lang_group.setTitle(tr("Language"))
        self.lang_label.setText(tr("Interface Language:"))
        auto_idx = self.language_combo.findData("auto")
        if auto_idx >= 0:
            self.language_combo.setItemText(auto_idx, tr("Auto-detect"))
        self.autostart_group.setTitle(tr("System Startup"))
        self.autostart_check.setText(tr("Start SemSearch automatically on desktop login"))


class SettingsDialog(QDialog):
    """Four-tab preferences dialog for directory, model, editor, and system configuration."""

    reindex_requested = Signal()
    force_reindex_requested = Signal()
    settings_saved = Signal()

    def __init__(
        self,
        config_manager: ConfigManager,
        ollama_client: OllamaClient,
        db: Database | None = None,
        code_db: Database | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config_manager = config_manager
        self.ollama = ollama_client
        self.db = db
        self.code_db = code_db

        self.setWindowTitle(tr("SemSearch Settings"))
        self.resize(720, 620)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        self.tabs = QTabWidget()
        self.folders_tab = FoldersTab()
        self.code_tab = CodeTab()
        self.storage_tab = StorageModelTab(self.ollama, self.db, self.code_db)
        self.storage_tab.reindex_requested.connect(self.reindex_requested.emit)
        self.storage_tab.force_reindex_requested.connect(self.force_reindex_requested.emit)
        self.general_tab = GeneralTab()

        self.tabs.addTab(self.folders_tab, tr("Folders"))
        self.tabs.addTab(self.code_tab, tr("Code"))
        self.tabs.addTab(self.storage_tab, qt_escape_amp(tr("Storage && Model")))
        self.tabs.addTab(self.general_tab, tr("General"))

        main_layout.addWidget(self.tabs, stretch=1)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self.save_settings)
        self.button_box.rejected.connect(self.reject)
        main_layout.addWidget(self.button_box)
        self._localize_buttons()

        apply_theme(self)
        self.load_settings()

    def set_hotkey_status(self, backend_name: str, error: str = "") -> None:
        """Update global shortcut backend status displayed in the General tab."""
        self.general_tab.set_hotkey_status(backend_name, error)

    def _localize_buttons(self) -> None:
        save_btn = self.button_box.button(QDialogButtonBox.StandardButton.Save)
        if save_btn is not None:
            save_btn.setText(tr("Save"))
        cancel_btn = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setText(tr("Cancel"))

    def retranslate_ui(self) -> None:
        """Refresh translated labels across dialog and tabs."""
        self.setWindowTitle(tr("SemSearch Settings"))
        self.tabs.setTabText(0, tr("Folders"))
        self.tabs.setTabText(1, tr("Code"))
        self.tabs.setTabText(2, qt_escape_amp(tr("Storage && Model")))
        self.tabs.setTabText(3, tr("General"))
        self._localize_buttons()
        self.folders_tab.retranslate_ui()
        self.code_tab.retranslate_ui()
        self.storage_tab.retranslate_ui()
        self.general_tab.retranslate_ui()

    def load_settings(self) -> None:
        """Load persistent config into tab widgets without triggering fake connection claims."""
        cfg = self.config_manager.config
        self.retranslate_ui()

        self.folders_tab.folders_editor.set_folders(cfg.folders)
        self.folders_tab.skipped_folders_edit.setText(", ".join(cfg.excluded_folders))
        self.folders_tab.skipped_ext_edit.setText(", ".join(cfg.excluded_types))
        self.folders_tab.battery_check.setChecked(cfg.index_on_battery)
        self.folders_tab.schedule_check.setChecked(cfg.index_schedule_enabled)
        start_h, start_m = divmod(cfg.index_schedule_start, 60)
        end_h, end_m = divmod(cfg.index_schedule_end, 60)
        self.folders_tab.start_time_edit.setTime(QTime(start_h, start_m))
        self.folders_tab.end_time_edit.setTime(QTime(end_h, end_m))

        self.code_tab.code_enable_check.setChecked(cfg.code_enabled)
        self.code_tab.code_folders_editor.set_folders(cfg.code_folders)
        idx = self.code_tab.editor_combo.findData(cfg.code_editor)
        if idx >= 0:
            self.code_tab.editor_combo.setCurrentIndex(idx)

        self.storage_tab.ollama_picker.set_url(cfg.ollama_url)
        self.storage_tab.ollama_picker.set_configured_model(cfg.model)
        self.storage_tab.num_ctx_edit.setText(str(cfg.num_ctx))
        self.storage_tab.update_database_stats()

        self.general_tab.hotkey_edit.setText(cfg.hotkey)
        lang_idx = self.general_tab.language_combo.findData(cfg.language)
        if lang_idx >= 0:
            self.general_tab.language_combo.setCurrentIndex(lang_idx)
        else:
            self.general_tab.language_combo.setCurrentIndex(0)
        self.general_tab.autostart_check.setChecked(is_autostart_enabled())

    def save_settings(self) -> None:
        """Validate inputs and persist settings from tab widgets back to ConfigManager."""
        validated_ctx = self.storage_tab.validate_num_ctx()
        if validated_ctx is None:
            self.tabs.setCurrentWidget(self.storage_tab)
            return

        hotkey = self.general_tab.hotkey_edit.text().strip()
        try:
            normalize_hotkey(hotkey)
        except ValueError as err:
            self.general_tab.status_label.setText(f"⚠ {err}")
            self.general_tab.status_label.setStyleSheet(f"color: {ThemeColors.AMBER};")
            self.tabs.setCurrentWidget(self.general_tab)
            return

        cfg = self.config_manager.config

        cfg.folders = self.folders_tab.folders_editor.get_folders()
        cfg.excluded_folders = [s.strip() for s in self.folders_tab.skipped_folders_edit.text().split(",") if s.strip()]
        cfg.excluded_types = [
            s.strip().lstrip(".") for s in self.folders_tab.skipped_ext_edit.text().split(",") if s.strip()
        ]
        cfg.index_on_battery = self.folders_tab.battery_check.isChecked()
        cfg.index_schedule_enabled = self.folders_tab.schedule_check.isChecked()
        s_time = self.folders_tab.start_time_edit.time()
        cfg.index_schedule_start = s_time.hour() * 60 + s_time.minute()
        e_time = self.folders_tab.end_time_edit.time()
        cfg.index_schedule_end = e_time.hour() * 60 + e_time.minute()

        cfg.code_enabled = self.code_tab.code_enable_check.isChecked()
        cfg.code_folders = self.code_tab.code_folders_editor.get_folders()
        cfg.code_editor = self.code_tab.editor_combo.currentData()

        cfg.ollama_url = self.storage_tab.ollama_picker.url()
        selected_model = self.storage_tab.ollama_picker.selected_model()
        if selected_model:
            cfg.model = selected_model
        cfg.num_ctx = validated_ctx

        cfg.hotkey = hotkey
        selected_lang = self.general_tab.language_combo.currentData()
        if selected_lang:
            cfg.language = selected_lang
        set_autostart_enabled(enabled=self.general_tab.autostart_check.isChecked())

        self.config_manager.save()
        self.settings_saved.emit()
        self.accept()

    def shutdown(self) -> None:
        """Stop and join any running background threads owned by settings tabs."""
        self.storage_tab.shutdown()

    def done(self, result: int) -> None:
        self.shutdown()
        super().done(result)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.shutdown()
        super().closeEvent(event)
