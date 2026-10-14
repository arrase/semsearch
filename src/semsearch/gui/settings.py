"""Settings dialog with four configuration tabs and verified multimodal model filtering."""

from pathlib import Path
import shutil
from PySide6.QtCore import QTime, Signal
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


def set_autostart_enabled(enabled: bool) -> None:
    """Create or remove autostart desktop entry."""
    if enabled:
        AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
        local_bin = Path.home() / ".local" / "bin" / "semsearch"
        exec_path = shutil.which("semsearch") or (
            str(local_bin) if local_bin.is_file() else "semsearch"
        )
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=SemSearch\n"
            "Comment=Linux desktop semantic search engine\n"
            f"Exec={exec_path} --daemon\n"
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

        self.folders_group = QGroupBox(tr("Indexed Folders"))
        folders_layout = QVBoxLayout(self.folders_group)
        self.folders_list = QListWidget()
        self.folders_list.setMinimumHeight(110)
        btn_row = QHBoxLayout()
        self.add_folder_btn = QPushButton(tr("Add Folder..."))
        self.add_folder_btn.clicked.connect(self._add_folder)
        self.remove_folder_btn = QPushButton(tr("Remove"))
        self.remove_folder_btn.clicked.connect(self._remove_folder)
        btn_row.addWidget(self.add_folder_btn)
        btn_row.addWidget(self.remove_folder_btn)
        btn_row.addStretch()

        folders_layout.addWidget(self.folders_list, stretch=1)
        folders_layout.addLayout(btn_row)
        layout.addWidget(self.folders_group, stretch=1)

        self.excl_group = QGroupBox(tr("Exclusions"))
        excl_layout = QFormLayout(self.excl_group)
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
        layout.addWidget(self.excl_group)

        self.power_group = QGroupBox(tr("Power && Indexing Schedule"))
        power_layout = QVBoxLayout(self.power_group)
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
        layout.addWidget(self.power_group)

    def retranslate_ui(self) -> None:
        """Refresh translated labels in FoldersTab."""
        self.folders_group.setTitle(tr("Indexed Folders"))
        self.add_folder_btn.setText(tr("Add Folder..."))
        self.remove_folder_btn.setText(tr("Remove"))
        self.excl_group.setTitle(tr("Exclusions"))
        self.skipped_folders_edit.setPlaceholderText(
            tr("comma-separated folder names e.g. node_modules, .venv, target")
        )
        self.skipped_ext_edit.setPlaceholderText(tr("comma-separated extensions e.g. heic, log"))
        self.skipped_folders_label.setText(tr("Skipped Folders:"))
        self.skipped_ext_label.setText(tr("Skipped Extensions:"))
        self.power_group.setTitle(tr("Power && Indexing Schedule"))
        self.battery_check.setText(tr("Index while on battery power"))
        self.schedule_check.setText(tr("Limit indexing to hours:"))
        self.to_label.setText(tr("to"))

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Select Folder to Index"))
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

        self.code_enable_check = QCheckBox(tr("Enable Source Code Search"))
        self.code_enable_check.setFont(get_ui_font(13, get_ui_font().weight()))
        layout.addWidget(self.code_enable_check)

        # Code Folders / Repos
        self.code_folders_group = QGroupBox(tr("Code Root Directories"))
        code_layout = QVBoxLayout(self.code_folders_group)
        self.code_folders_list = QListWidget()
        btn_row = QHBoxLayout()
        self.add_code_btn = QPushButton(tr("Add Code Root..."))
        self.add_code_btn.clicked.connect(self._add_code_folder)
        self.remove_code_btn = QPushButton(tr("Remove"))
        self.remove_code_btn.clicked.connect(self._remove_code_folder)
        btn_row.addWidget(self.add_code_btn)
        btn_row.addWidget(self.remove_code_btn)
        btn_row.addStretch()

        code_layout.addWidget(self.code_folders_list)
        code_layout.addLayout(btn_row)
        layout.addWidget(self.code_folders_group)

        # Editor selection
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
        self.add_code_btn.setText(tr("Add Code Root..."))
        self.remove_code_btn.setText(tr("Remove"))
        self.editor_group.setTitle(tr("Preferred Code Editor"))
        self.open_with_label.setText(tr("Open code results with:"))
        auto_idx = self.editor_combo.findData("auto")
        if auto_idx >= 0:
            self.editor_combo.setItemText(auto_idx, tr("Auto-detect"))

    def _add_code_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Select Code Root Directory"))
        if folder:
            self.code_folders_list.addItem(folder)

    def _remove_code_folder(self) -> None:
        row = self.code_folders_list.currentRow()
        if row >= 0:
            self.code_folders_list.takeItem(row)


class StorageModelTab(QWidget):
    """Tab 3: Multimodal embedding model configuration and local database maintenance."""

    reindex_requested = Signal()

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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Ollama Connection & Model
        self.ollama_group = QGroupBox(tr("Multimodal Embedding Model (Ollama)"))
        ollama_layout = QFormLayout(self.ollama_group)

        url_row = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.test_btn = QPushButton(tr("Test Connection"))
        self.test_btn.clicked.connect(self.test_connection)
        url_row.addWidget(self.url_edit, stretch=1)
        url_row.addWidget(self.test_btn)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(False)

        self.num_ctx_edit = QLineEdit()
        self.num_ctx_edit.setPlaceholderText(tr("0 (Model default)"))

        self.status_label = QLabel()
        self.status_label.setFont(get_ui_font(11))

        self.host_url_label = QLabel(tr("Ollama Host URL:"))
        self.model_label = QLabel(tr("Multimodal Model:"))
        self.num_ctx_label = QLabel(tr("Context Window (num_ctx):"))
        ollama_layout.addRow(self.host_url_label, url_row)
        ollama_layout.addRow(self.model_label, self.model_combo)
        ollama_layout.addRow(self.num_ctx_label, self.num_ctx_edit)
        ollama_layout.addRow("", self.status_label)
        layout.addWidget(self.ollama_group)

        # Storage & Database Stats
        self.db_group = QGroupBox(tr("Database && Storage Usage"))
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
        self.test_btn.setText(tr("Test Connection"))
        self.host_url_label.setText(tr("Ollama Host URL:"))
        self.model_label.setText(tr("Multimodal Model:"))
        self.num_ctx_label.setText(tr("Context Window (num_ctx):"))
        self.num_ctx_edit.setPlaceholderText(tr("0 (Model default)"))
        self.db_group.setTitle(tr("Database && Storage Usage"))
        self.vacuum_btn.setText(tr("Optimize / Vacuum Database"))
        self.reindex_btn.setText(tr("Re-index All Files"))
        self.update_database_stats()

    def _on_reindex_clicked(self) -> None:
        self.reindex_requested.emit()

    def validate_num_ctx(self) -> int | None:
        """Validate user-entered context window size against the model's supported context_length."""
        raw = self.num_ctx_edit.text().strip()
        if not raw:
            return 0
        if not raw.isdigit():
            self.status_label.setText(
                tr("⚠ Invalid context window: enter a positive integer or 0 for default.")
            )
            self.status_label.setStyleSheet(f"color: {ThemeColors.AMBER};")
            return None

        value = int(raw)
        if value == 0:
            return 0

        self.ollama.base_url = self.url_edit.text().strip()
        selected_model = self.model_combo.currentText().strip() or "embeddinggemma-2"
        max_ctx = self.ollama.get_model_context_length(selected_model)
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
        """Query Ollama host and restrict model dropdown strictly to verified multimodal models."""
        self.ollama.base_url = self.url_edit.text().strip()
        try:
            models = self.ollama.list_multimodal_models()
            self.populate_multimodal_models(models)
            self.validate_num_ctx()
        except Exception as e:
            self.status_label.setText(tr("⚠ Connection failed: {error}", error=e))
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
            self.status_label.setText(
                tr("✓ Connected: {count} multimodal model(s) verified.", count=len(models))
            )
            self.status_label.setStyleSheet(f"color: {ThemeColors.LEAF};")
        else:
            self.model_combo.addItem("embeddinggemma-2")
            self.status_label.setText(
                tr("⚠ No multimodal models detected on Ollama host. Run 'ollama pull embeddinggemma-2'.")
            )
            self.status_label.setStyleSheet(f"color: {ThemeColors.AMBER};")

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
        if self.db is not None:
            self.db.vacuum()
        if self.code_db is not None:
            self.code_db.vacuum()
        self.update_database_stats()


class GeneralTab(QWidget):
    """Tab 4: Global desktop shortcut, interface language, and system autostart."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # Hotkey Group
        self.hotkey_group = QGroupBox(tr("Global Shortcut"))
        hotkey_layout = QFormLayout(self.hotkey_group)
        self.hotkey_edit = QLineEdit()
        self.hotkey_edit.setPlaceholderText("Ctrl+Shift+Space")
        self.hotkey_label = QLabel(tr("Quick Search Shortcut:"))
        hotkey_layout.addRow(self.hotkey_label, self.hotkey_edit)
        layout.addWidget(self.hotkey_group)

        # Language Group
        self.lang_group = QGroupBox(tr("Language"))
        lang_layout = QFormLayout(self.lang_group)
        self.language_combo = QComboBox()
        self.language_combo.addItem(tr("Auto-detect"), "auto")
        for code, name in SUPPORTED_LANGUAGES.items():
            self.language_combo.addItem(f"{name} ({code})", code)
        self.lang_label = QLabel(tr("Interface Language:"))
        lang_layout.addRow(self.lang_label, self.language_combo)
        layout.addWidget(self.lang_group)

        # Autostart Group
        self.autostart_group = QGroupBox(tr("System Startup"))
        autostart_layout = QVBoxLayout(self.autostart_group)
        self.autostart_check = QCheckBox(tr("Start SemSearch automatically on desktop login"))
        autostart_layout.addWidget(self.autostart_check)
        layout.addWidget(self.autostart_group)
        layout.addStretch()

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
        self.general_tab = GeneralTab()

        self.tabs.addTab(self.folders_tab, tr("Folders"))
        self.tabs.addTab(self.code_tab, tr("Code"))
        self.tabs.addTab(self.storage_tab, tr("Storage && Model"))
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
        self.tabs.setTabText(2, tr("Storage && Model"))
        self.tabs.setTabText(3, tr("General"))
        self._localize_buttons()
        self.folders_tab.retranslate_ui()
        self.code_tab.retranslate_ui()
        self.storage_tab.retranslate_ui()
        self.general_tab.retranslate_ui()

    def load_settings(self) -> None:
        """Load persistent config into tab widgets."""
        cfg = self.config_manager.config
        self.retranslate_ui()

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
        self.storage_tab.num_ctx_edit.setText(str(cfg.num_ctx))
        self.storage_tab.update_database_stats()

        # Tab 4: General
        self.general_tab.hotkey_edit.setText(cfg.hotkey)
        lang_idx = self.general_tab.language_combo.findData(cfg.language)
        if lang_idx >= 0:
            self.general_tab.language_combo.setCurrentIndex(lang_idx)
        else:
            self.general_tab.language_combo.setCurrentIndex(0)
        self.general_tab.autostart_check.setChecked(is_autostart_enabled())

    def save_settings(self) -> None:
        """Save settings from tab widgets back to ConfigManager."""
        validated_ctx = self.storage_tab.validate_num_ctx()
        if validated_ctx is None:
            self.tabs.setCurrentWidget(self.storage_tab)
            return

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
        cfg.num_ctx = validated_ctx

        # Tab 4: General
        hotkey = self.general_tab.hotkey_edit.text().strip()
        if hotkey:
            cfg.hotkey = hotkey
        selected_lang = self.general_tab.language_combo.currentData()
        if selected_lang:
            cfg.language = selected_lang
        set_autostart_enabled(self.general_tab.autostart_check.isChecked())

        self.config_manager.save()
        self.settings_saved.emit()
        self.accept()
