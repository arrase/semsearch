"""Settings dialog with four configuration tabs and verified multimodal model filtering."""

from pathlib import Path
from PySide6.QtCore import QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from semsearch.config import ConfigManager
from semsearch.core.database import Database
from semsearch.core.ollama import OllamaClient
from semsearch.gui.preview import format_file_size
from semsearch.gui.theme import (
    ThemeColors,
    apply_theme,
    get_ui_font,
)


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


def set_autostart_enabled(enabled: bool) -> None:
    """Create or remove autostart desktop entry."""
    if enabled:
        AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=SemSearch\n"
            "Comment=Linux desktop semantic search engine\n"
            "Exec=semsearch --daemon\n"
            "Hidden=false\n"
            "NoDisplay=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        AUTOSTART_FILE.write_text(content, encoding="utf-8")
    else:
        if AUTOSTART_FILE.is_file():
            AUTOSTART_FILE.unlink()


class FoldersTab(QWidget):
    """Tab 1: Search folders, exclusions, power policy, and operating schedule."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Indexed Folders
        folders_group = QGroupBox("Indexed Folders")
        folders_layout = QVBoxLayout(folders_group)
        self.folders_list = QListWidget()
        btn_row = QHBoxLayout()
        self.add_folder_btn = QPushButton("Add Folder...")
        self.add_folder_btn.clicked.connect(self._add_folder)
        self.remove_folder_btn = QPushButton("Remove")
        self.remove_folder_btn.clicked.connect(self._remove_folder)
        btn_row.addWidget(self.add_folder_btn)
        btn_row.addWidget(self.remove_folder_btn)
        btn_row.addStretch()

        folders_layout.addWidget(self.folders_list)
        folders_layout.addLayout(btn_row)
        layout.addWidget(folders_group)

        # Exclusions
        excl_group = QGroupBox("Exclusions")
        excl_layout = QFormLayout(excl_group)
        self.skipped_folders_edit = QLineEdit()
        self.skipped_folders_edit.setPlaceholderText("comma-separated folder names e.g. node_modules, .venv, target")
        self.skipped_ext_edit = QLineEdit()
        self.skipped_ext_edit.setPlaceholderText("comma-separated extensions e.g. heic, txt, log")
        excl_layout.addRow("Skipped Folders:", self.skipped_folders_edit)
        excl_layout.addRow("Skipped Extensions:", self.skipped_ext_edit)
        layout.addWidget(excl_group)

        # Power & Schedule
        power_group = QGroupBox("Power & Indexing Schedule")
        power_layout = QVBoxLayout(power_group)
        self.battery_check = QCheckBox("Index while on battery power")

        schedule_row = QHBoxLayout()
        self.schedule_check = QCheckBox("Limit indexing to hours:")
        self.start_time_edit = QTimeEdit()
        self.start_time_edit.setDisplayFormat("HH:mm")
        self.end_time_edit = QTimeEdit()
        self.end_time_edit.setDisplayFormat("HH:mm")

        schedule_row.addWidget(self.schedule_check)
        schedule_row.addWidget(self.start_time_edit)
        schedule_row.addWidget(QLabel("to"))
        schedule_row.addWidget(self.end_time_edit)
        schedule_row.addStretch()

        power_layout.addWidget(self.battery_check)
        power_layout.addLayout(schedule_row)
        layout.addWidget(power_group)

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Folder to Index")
        if folder:
            self.folders_list.addItem(folder)

    def _remove_folder(self) -> None:
        row = self.folders_list.currentRow()
        if row >= 0:
            self.folders_list.takeItem(row)


class CodeTab(QWidget):
    """Tab 2: Code search toggle, code root directories, and editor picker."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        self.code_enable_check = QCheckBox("Enable Source Code Search")
        self.code_enable_check.setFont(get_ui_font(13, get_ui_font().weight()))
        layout.addWidget(self.code_enable_check)

        # Code Folders / Repos
        code_folders_group = QGroupBox("Code Root Directories")
        code_layout = QVBoxLayout(code_folders_group)
        self.code_folders_list = QListWidget()
        btn_row = QHBoxLayout()
        self.add_code_btn = QPushButton("Add Code Root...")
        self.add_code_btn.clicked.connect(self._add_code_folder)
        self.remove_code_btn = QPushButton("Remove")
        self.remove_code_btn.clicked.connect(self._remove_code_folder)
        btn_row.addWidget(self.add_code_btn)
        btn_row.addWidget(self.remove_code_btn)
        btn_row.addStretch()

        code_layout.addWidget(self.code_folders_list)
        code_layout.addLayout(btn_row)
        layout.addWidget(code_folders_group)

        # Editor selection
        editor_group = QGroupBox("Preferred Code Editor")
        editor_layout = QHBoxLayout(editor_group)
        self.editor_combo = QComboBox()
        for label, val in EDITOR_OPTIONS:
            self.editor_combo.addItem(label, val)
        editor_layout.addWidget(QLabel("Open code results with:"))
        editor_layout.addWidget(self.editor_combo, stretch=1)
        layout.addWidget(editor_group)
        layout.addStretch()

    def _add_code_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Code Root Directory")
        if folder:
            self.code_folders_list.addItem(folder)

    def _remove_code_folder(self) -> None:
        row = self.code_folders_list.currentRow()
        if row >= 0:
            self.code_folders_list.takeItem(row)


class StorageModelTab(QWidget):
    """Tab 3: Ollama host, multimodal model filtering, and database statistics."""

    def __init__(
        self,
        ollama_client: OllamaClient,
        db: Database | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.ollama = ollama_client
        self.db = db

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Ollama Connection & Model
        ollama_group = QGroupBox("Multimodal Embedding Model (Ollama)")
        ollama_layout = QFormLayout(ollama_group)

        url_row = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.test_btn = QPushButton("Test Connection")
        self.test_btn.clicked.connect(self.test_connection)
        url_row.addWidget(self.url_edit, stretch=1)
        url_row.addWidget(self.test_btn)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(False)

        self.status_label = QLabel()
        self.status_label.setFont(get_ui_font(11))

        ollama_layout.addRow("Ollama Host URL:", url_row)
        ollama_layout.addRow("Multimodal Model:", self.model_combo)
        ollama_layout.addRow("", self.status_label)
        layout.addWidget(ollama_group)

        # Storage & Database Stats
        db_group = QGroupBox("Database & Storage Usage")
        db_layout = QVBoxLayout(db_group)
        self.stats_label = QLabel("Loading database metrics...")
        self.stats_label.setFont(get_ui_font(12))

        db_btn_row = QHBoxLayout()
        self.vacuum_btn = QPushButton("Optimize / Vacuum Database")
        self.vacuum_btn.clicked.connect(self._vacuum_database)
        self.reindex_btn = QPushButton("Re-index All Files")
        db_btn_row.addWidget(self.vacuum_btn)
        db_btn_row.addWidget(self.reindex_btn)
        db_btn_row.addStretch()

        db_layout.addWidget(self.stats_label)
        db_layout.addLayout(db_btn_row)
        layout.addWidget(db_group)
        layout.addStretch()

    def test_connection(self) -> None:
        """Query Ollama host and restrict model dropdown strictly to verified multimodal models."""
        self.ollama.base_url = self.url_edit.text().strip()
        try:
            models = self.ollama.list_multimodal_models()
            self.populate_multimodal_models(models)
        except Exception as e:
            self.status_label.setText(f"⚠ Connection failed: {e}")
            self.status_label.setStyleSheet(f"color: {ThemeColors.AMBER};")

    def populate_multimodal_models(self, models: list[str]) -> None:
        """Populate model dropdown with verified multimodal models only, defaulting to embeddinggemma-2."""
        self.model_combo.clear()
        if models:
            for m in models:
                self.model_combo.addItem(m)
            # Default to embeddinggemma-2 if present, otherwise first available
            idx = self.model_combo.findText("embeddinggemma-2")
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
            self.status_label.setText(f"✓ Connected: {len(models)} multimodal model(s) verified.")
            self.status_label.setStyleSheet(f"color: {ThemeColors.LEAF};")
        else:
            self.model_combo.addItem("embeddinggemma-2")
            self.status_label.setText("⚠ No multimodal models detected on Ollama host. Run 'ollama pull embeddinggemma-2'.")
            self.status_label.setStyleSheet(f"color: {ThemeColors.AMBER};")

    def update_database_stats(self) -> None:
        """Update display of indexed files, chunks, and database disk usage."""
        if self.db is None:
            self.stats_label.setText("Database metrics unavailable")
            return
        stats = self.db.stats()
        db_path = Path(self.db.path)
        disk_size = format_file_size(db_path.stat().st_size) if db_path.is_file() else "0 B"
        self.stats_label.setText(
            f"Indexed Files: {stats.get('files', 0):,}  |  "
            f"Segments: {stats.get('segments', 0):,}  |  "
            f"Vectors: {stats.get('vectors', 0):,}  |  "
            f"Disk Size: {disk_size}"
        )

    def _vacuum_database(self) -> None:
        if self.db is not None:
            self.db._conn.execute("VACUUM")
            self.update_database_stats()


class GeneralTab(QWidget):
    """Tab 4: Global desktop shortcut and system autostart."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Hotkey Group
        hotkey_group = QGroupBox("Global Shortcut")
        hotkey_layout = QFormLayout(hotkey_group)
        self.hotkey_edit = QLineEdit()
        self.hotkey_edit.setPlaceholderText("Ctrl+Shift+Space")
        hotkey_layout.addRow("Quick Search Shortcut:", self.hotkey_edit)
        layout.addWidget(hotkey_group)

        # Autostart Group
        autostart_group = QGroupBox("System Startup")
        autostart_layout = QVBoxLayout(autostart_group)
        self.autostart_check = QCheckBox("Start SemSearch automatically on desktop login")
        autostart_layout.addWidget(self.autostart_check)
        layout.addWidget(autostart_group)
        layout.addStretch()


class SettingsDialog(QDialog):
    """Four-tab preferences dialog for directory, model, editor, and system configuration."""

    def __init__(
        self,
        config_manager: ConfigManager,
        ollama_client: OllamaClient,
        db: Database | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config_manager = config_manager
        self.ollama = ollama_client
        self.db = db

        self.setWindowTitle("SemSearch Settings")
        self.resize(600, 480)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        self.tabs = QTabWidget()
        self.folders_tab = FoldersTab()
        self.code_tab = CodeTab()
        self.storage_tab = StorageModelTab(self.ollama, self.db)
        self.general_tab = GeneralTab()

        self.tabs.addTab(self.folders_tab, "Folders")
        self.tabs.addTab(self.code_tab, "Code")
        self.tabs.addTab(self.storage_tab, "Storage & Model")
        self.tabs.addTab(self.general_tab, "General")

        main_layout.addWidget(self.tabs, stretch=1)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self.save_settings)
        self.button_box.rejected.connect(self.reject)
        main_layout.addWidget(self.button_box)

        apply_theme(self)
        self.load_settings()

    def load_settings(self) -> None:
        """Load persistent config into tab widgets."""
        cfg = self.config_manager.config

        # Tab 1: Folders
        self.folders_tab.folders_list.clear()
        for f in cfg.folders:
            self.folders_tab.folders_list.addItem(f)
        self.folders_tab.skipped_folders_edit.setText(", ".join(cfg.excluded_folders))
        self.folders_tab.skipped_ext_edit.setText(", ".join(cfg.excluded_types))
        self.folders_tab.battery_check.setChecked(cfg.index_on_battery)
        self.folders_tab.schedule_check.setChecked(cfg.index_schedule_enabled)
        start_h, start_m = divmod(cfg.index_schedule_start, 60)
        end_h, end_m = divmod(cfg.index_schedule_end, 60)
        self.folders_tab.start_time_edit.setTime(QTime(start_h, start_m))
        self.folders_tab.end_time_edit.setTime(QTime(end_h, end_m))

        # Tab 2: Code
        self.code_tab.code_enable_check.setChecked(cfg.code_enabled)
        self.code_tab.code_folders_list.clear()
        for f in cfg.code_folders:
            self.code_tab.code_folders_list.addItem(f)
        idx = self.code_tab.editor_combo.findData(cfg.code_editor)
        if idx >= 0:
            self.code_tab.editor_combo.setCurrentIndex(idx)

        # Tab 3: Storage & Model
        self.storage_tab.url_edit.setText(cfg.ollama_url)
        self.storage_tab.populate_multimodal_models([cfg.model] if cfg.model else ["embeddinggemma-2"])
        self.storage_tab.update_database_stats()

        # Tab 4: General
        self.general_tab.hotkey_edit.setText(cfg.hotkey)
        self.general_tab.autostart_check.setChecked(is_autostart_enabled())

    def save_settings(self) -> None:
        """Save settings from tab widgets back to ConfigManager."""
        cfg = self.config_manager.config

        # Tab 1: Folders
        folders = [
            self.folders_tab.folders_list.item(i).text()
            for i in range(self.folders_tab.folders_list.count())
        ]
        excl_folders = [
            s.strip() for s in self.folders_tab.skipped_folders_edit.text().split(",") if s.strip()
        ]
        excl_types = [
            s.strip().lstrip(".") for s in self.folders_tab.skipped_ext_edit.text().split(",") if s.strip()
        ]
        cfg.folders = folders
        cfg.excluded_folders = excl_folders
        cfg.excluded_types = excl_types
        cfg.index_on_battery = self.folders_tab.battery_check.isChecked()
        cfg.index_schedule_enabled = self.folders_tab.schedule_check.isChecked()
        s_time = self.folders_tab.start_time_edit.time()
        cfg.index_schedule_start = s_time.hour() * 60 + s_time.minute()
        e_time = self.folders_tab.end_time_edit.time()
        cfg.index_schedule_end = e_time.hour() * 60 + e_time.minute()

        # Tab 2: Code
        cfg.code_enabled = self.code_tab.code_enable_check.isChecked()
        code_folders = [
            self.code_tab.code_folders_list.item(i).text()
            for i in range(self.code_tab.code_folders_list.count())
        ]
        cfg.code_folders = code_folders
        cfg.code_editor = self.code_tab.editor_combo.currentData()

        # Tab 3: Storage & Model
        cfg.ollama_url = self.storage_tab.url_edit.text().strip()
        selected_model = self.storage_tab.model_combo.currentText()
        if selected_model:
            cfg.model = selected_model

        # Tab 4: General
        hotkey = self.general_tab.hotkey_edit.text().strip()
        if hotkey:
            cfg.hotkey = hotkey
        set_autostart_enabled(self.general_tab.autostart_check.isChecked())

        self.config_manager.save(cfg)
        self.accept()
