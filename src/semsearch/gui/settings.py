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
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from semsearch.config import AppConfig, ConfigManager, get_autostart_path
from semsearch.core.database import Database
from semsearch.core.ollama import OllamaClient
from semsearch.gui.hotkey import normalize_hotkey
from semsearch.gui.preview import format_file_size
from semsearch.gui.theme import apply_theme, get_palette, get_ui_font
from semsearch.gui.widgets import DEFAULT_MODEL, FolderListEditor, OllamaModelPicker, qt_escape_amp
from semsearch.i18n import SUPPORTED_LANGUAGES, tr

_DEFAULT_AUTOSTART_DIR = Path.home() / ".config" / "autostart"
_DEFAULT_AUTOSTART_FILE = _DEFAULT_AUTOSTART_DIR / "semsearch.desktop"
AUTOSTART_DIR = _DEFAULT_AUTOSTART_DIR
AUTOSTART_FILE = _DEFAULT_AUTOSTART_FILE

_TRANSLATABLE_OPTION_VALUES = frozenset({"auto", "custom"})

EDITOR_OPTIONS = [
    ("Auto-detect", "auto"),
    ("VS Code", "code"),
    ("Cursor", "cursor"),
    ("Zed", "zed"),
    ("Sublime Text", "subl"),
    ("JetBrains", "jetbrains"),
    ("Kate", "kate"),
    ("Geany", "geany"),
    ("Neovide", "neovide"),
    ("Emacs (emacsclient)", "emacsclient"),
    ("Custom Command...", "custom"),
]

KEEP_ALIVE_OPTIONS = [
    ("Unload when idle (saves VRAM)", "unload_idle"),
    ("Use Ollama server default", "server"),
    ("Keep model loaded in VRAM", "keep_loaded"),
]

THEME_OPTIONS = [
    ("Dark", "dark"),
    ("Light", "light"),
    ("System", "system"),
]

TERMINAL_OPTIONS = [
    ("Auto-detect", "auto"),
    ("xdg-terminal-exec", "xdg-terminal-exec"),
    ("kitty", "kitty"),
    ("alacritty", "alacritty"),
    ("ghostty", "ghostty"),
    ("konsole", "konsole"),
    ("gnome-terminal", "gnome-terminal"),
    ("ptyxis", "ptyxis"),
    ("foot", "foot"),
    ("wezterm", "wezterm"),
    ("xfce4-terminal", "xfce4-terminal"),
    ("tilix", "tilix"),
    ("cosmic-term", "cosmic-term"),
    ("xterm", "xterm"),
    ("Custom Command...", "custom"),
]


def _resolve_autostart_file() -> Path:
    if AUTOSTART_FILE != _DEFAULT_AUTOSTART_FILE:
        return AUTOSTART_FILE
    if AUTOSTART_DIR != _DEFAULT_AUTOSTART_DIR:
        return AUTOSTART_DIR / "semsearch.desktop"
    return get_autostart_path()


def is_autostart_enabled() -> bool:
    """Check if autostart desktop entry exists."""
    return _resolve_autostart_file().is_file()


def set_autostart_enabled(*, enabled: bool) -> None:
    """Create or remove autostart desktop entry."""
    autostart_file = _resolve_autostart_file()
    if enabled:
        autostart_file.parent.mkdir(parents=True, exist_ok=True)
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
        autostart_file.write_text(content, encoding="utf-8")
    elif autostart_file.is_file():
        autostart_file.unlink()


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
    """Tab 1: Search folders, exclusions, indexing behavior, power policy, and operating schedule."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

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
        self.folders_list.setMinimumHeight(90)
        self.add_folder_btn = self.folders_editor.add_btn
        self.remove_folder_btn = self.folders_editor.remove_btn
        folders_layout.addWidget(self.folders_editor, stretch=1)
        return group

    def _build_exclusions_group(self) -> QGroupBox:
        group = QGroupBox(tr("Exclusions"))
        excl_layout = QFormLayout(group)
        excl_layout.setSpacing(6)
        self.skipped_folders_edit = QLineEdit()
        self.skipped_folders_edit.setPlaceholderText(
            tr("comma-separated folder names e.g. node_modules, .venv, target")
        )
        self.skipped_ext_edit = QLineEdit()
        self.skipped_ext_edit.setPlaceholderText(tr("comma-separated e.g. heic, video, audio"))
        self.skipped_folders_label = QLabel(tr("Skipped Folders:"))
        self.skipped_ext_label = QLabel(tr("Skipped Extensions / Kinds:"))

        self.max_media_duration_label = QLabel(tr("Max media duration (min, 0 = unlimited):"))
        self.max_media_duration_spin = QSpinBox()
        self.max_media_duration_spin.setRange(0, 1440)
        self.max_media_duration_spin.setValue(60)

        self.follow_symlinks_check = QCheckBox(tr("Follow symbolic links"))
        self.skip_code_projects_check = QCheckBox(tr("Skip code repositories inside general folders"))
        self.skip_datasets_check = QCheckBox(tr("Skip machine-generated dataset folders"))

        excl_layout.addRow(self.skipped_folders_label, self.skipped_folders_edit)
        excl_layout.addRow(self.skipped_ext_label, self.skipped_ext_edit)
        excl_layout.addRow(self.max_media_duration_label, self.max_media_duration_spin)
        excl_layout.addRow(self.follow_symlinks_check)
        excl_layout.addRow(self.skip_code_projects_check)
        excl_layout.addRow(self.skip_datasets_check)
        return group

    def _build_power_group(self) -> QGroupBox:
        group = QGroupBox(qt_escape_amp(tr("Power && Indexing Schedule")))
        power_layout = QVBoxLayout(group)
        power_layout.setSpacing(6)
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

    def load_config(self, cfg: AppConfig) -> None:
        """Populate FoldersTab controls from AppConfig."""
        self.folders_editor.set_folders(cfg.folders)
        self.skipped_folders_edit.setText(", ".join(cfg.excluded_folders))
        self.skipped_ext_edit.setText(", ".join(cfg.excluded_types))
        self.follow_symlinks_check.setChecked(cfg.follow_symlinks)
        self.skip_code_projects_check.setChecked(cfg.skip_code_projects)
        self.skip_datasets_check.setChecked(cfg.skip_datasets)
        self.max_media_duration_spin.setValue(cfg.max_media_duration_min)
        self.battery_check.setChecked(cfg.index_on_battery)
        self.schedule_check.setChecked(cfg.index_schedule_enabled)
        start_h, start_m = divmod(cfg.index_schedule_start, 60)
        end_h, end_m = divmod(cfg.index_schedule_end, 60)
        self.start_time_edit.setTime(QTime(start_h, start_m))
        self.end_time_edit.setTime(QTime(end_h, end_m))

    def save_config(self, cfg: AppConfig) -> None:
        """Persist FoldersTab values into AppConfig."""
        cfg.folders = self.folders_editor.get_folders()
        cfg.excluded_folders = [s.strip() for s in self.skipped_folders_edit.text().split(",") if s.strip()]
        cfg.excluded_types = [s.strip().lstrip(".") for s in self.skipped_ext_edit.text().split(",") if s.strip()]
        cfg.follow_symlinks = self.follow_symlinks_check.isChecked()
        cfg.skip_code_projects = self.skip_code_projects_check.isChecked()
        cfg.skip_datasets = self.skip_datasets_check.isChecked()
        cfg.max_media_duration_min = self.max_media_duration_spin.value()
        cfg.index_on_battery = self.battery_check.isChecked()
        cfg.index_schedule_enabled = self.schedule_check.isChecked()
        s_time = self.start_time_edit.time()
        cfg.index_schedule_start = s_time.hour() * 60 + s_time.minute()
        e_time = self.end_time_edit.time()
        cfg.index_schedule_end = e_time.hour() * 60 + e_time.minute()

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
        self.skipped_ext_edit.setPlaceholderText(tr("comma-separated e.g. heic, video, audio"))
        self.skipped_folders_label.setText(tr("Skipped Folders:"))
        self.skipped_ext_label.setText(tr("Skipped Extensions / Kinds:"))
        self.max_media_duration_label.setText(tr("Max media duration (min, 0 = unlimited):"))
        self.follow_symlinks_check.setText(tr("Follow symbolic links"))
        self.skip_code_projects_check.setText(tr("Skip code repositories inside general folders"))
        self.skip_datasets_check.setText(tr("Skip machine-generated dataset folders"))
        self.power_group.setTitle(qt_escape_amp(tr("Power && Indexing Schedule")))
        self.battery_check.setText(tr("Index while on battery power"))
        self.schedule_check.setText(tr("Limit indexing to hours:"))
        self.to_label.setText(tr("to"))


class CodeTab(QWidget):
    """Tab 2: Code search toggle, code root directories, watch delay, and editor picker."""

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

        delay_row = QHBoxLayout()
        delay_row.setSpacing(8)
        self.code_watch_delay_label = QLabel(tr("Live re-index delay (seconds):"))
        self.code_watch_delay_spin = QSpinBox()
        self.code_watch_delay_spin.setRange(1, 3600)
        self.code_watch_delay_spin.setValue(60)
        delay_row.addWidget(self.code_watch_delay_label)
        delay_row.addWidget(self.code_watch_delay_spin)
        delay_row.addStretch()
        code_layout.addLayout(delay_row)
        layout.addWidget(self.code_folders_group)

        self.editor_group = QGroupBox(tr("Preferred Code Editor"))
        editor_layout = QFormLayout(self.editor_group)
        editor_layout.setSpacing(8)
        self.editor_combo = QComboBox()
        for label, val in EDITOR_OPTIONS:
            self.editor_combo.addItem(tr(label) if val in _TRANSLATABLE_OPTION_VALUES else label, val)
        self.editor_combo.currentIndexChanged.connect(self._update_custom_editor_visibility)
        self.open_with_label = QLabel(tr("Open code results with:"))
        self.custom_editor_label = QLabel(tr("Custom editor command:"))
        self.custom_editor_edit = QLineEdit()
        self.custom_editor_edit.setPlaceholderText("nvim-qt +{line} {file}")
        editor_layout.addRow(self.open_with_label, self.editor_combo)
        editor_layout.addRow(self.custom_editor_label, self.custom_editor_edit)
        self._update_custom_editor_visibility()
        layout.addWidget(self.editor_group)
        layout.addStretch()

    def _update_custom_editor_visibility(self) -> None:
        is_custom = self.editor_combo.currentData() == "custom"
        self.custom_editor_label.setVisible(is_custom)
        self.custom_editor_edit.setVisible(is_custom)

    def load_config(self, cfg: AppConfig) -> None:
        """Populate CodeTab controls from AppConfig, preserving unknown manual editor commands."""
        self.code_enable_check.setChecked(cfg.code_enabled)
        self.code_folders_editor.set_folders(cfg.code_folders)
        self.code_watch_delay_spin.setValue(cfg.code_watch_delay_sec)
        idx = self.editor_combo.findData(cfg.code_editor)
        if idx >= 0:
            self.editor_combo.setCurrentIndex(idx)
            self.custom_editor_edit.setText(cfg.custom_editor_cmd)
        else:
            custom_idx = self.editor_combo.findData("custom")
            self.editor_combo.setCurrentIndex(custom_idx)
            self.custom_editor_edit.setText(cfg.custom_editor_cmd or cfg.code_editor)
        self._update_custom_editor_visibility()

    def save_config(self, cfg: AppConfig) -> None:
        """Persist CodeTab values into AppConfig."""
        cfg.code_enabled = self.code_enable_check.isChecked()
        cfg.code_folders = self.code_folders_editor.get_folders()
        cfg.code_watch_delay_sec = self.code_watch_delay_spin.value()
        cfg.code_editor = self.editor_combo.currentData()
        cfg.custom_editor_cmd = self.custom_editor_edit.text().strip()

    def retranslate_ui(self) -> None:
        """Refresh translated labels in CodeTab."""
        self.code_enable_check.setText(tr("Enable Source Code Search"))
        self.code_folders_group.setTitle(tr("Code Root Directories"))
        self.code_folders_editor.retranslate_ui(
            tr("Add Code Root..."),
            tr("Remove"),
            tr("Select Code Root Directory"),
        )
        self.code_watch_delay_label.setText(tr("Live re-index delay (seconds):"))
        self.editor_group.setTitle(tr("Preferred Code Editor"))
        self.open_with_label.setText(tr("Open code results with:"))
        self.custom_editor_label.setText(tr("Custom editor command:"))
        for label, val in EDITOR_OPTIONS:
            if val in _TRANSLATABLE_OPTION_VALUES:
                idx = self.editor_combo.findData(val)
                if idx >= 0:
                    self.editor_combo.setItemText(idx, tr(label))


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

        self.ollama_group = self._build_ollama_group()
        self.db_group = self._build_db_group()
        layout.addWidget(self.ollama_group)
        layout.addWidget(self.db_group)
        layout.addStretch()

    def _build_ollama_group(self) -> QGroupBox:
        group = QGroupBox(tr("Multimodal Embedding Model (Ollama)"))
        ollama_group_layout = QVBoxLayout(group)
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

        self.keep_alive_label = QLabel(tr("VRAM Policy:"))
        self.keep_alive_combo = QComboBox()
        for label, val in KEEP_ALIVE_OPTIONS:
            self.keep_alive_combo.addItem(tr(label), val)
        self.ollama_picker.insert_row(self.keep_alive_label, self.keep_alive_combo)

        self.timeout_label = QLabel(tr("Embedding Timeout (seconds):"))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 3600)
        self.timeout_spin.setValue(120)
        self.ollama_picker.insert_row(self.timeout_label, self.timeout_spin)

        self.api_key_label = QLabel(tr("API Key:"))
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText(tr("Optional (or OLLAMA_API_KEY)"))
        self.api_key_edit.textChanged.connect(self.ollama_picker.set_api_key)
        self.ollama_picker.insert_row(self.api_key_label, self.api_key_edit)

        ollama_group_layout.addWidget(self.ollama_picker)
        return group

    def _build_db_group(self) -> QGroupBox:
        group = QGroupBox(qt_escape_amp(tr("Database && Storage Usage")))
        db_layout = QVBoxLayout(group)
        db_layout.setSpacing(8)

        data_dir_row = QHBoxLayout()
        data_dir_row.setSpacing(8)
        self.data_dir_label = QLabel(tr("Index Directory:"))
        self.data_dir_edit = QLineEdit()
        self.data_dir_edit.setPlaceholderText(tr("Default (XDG data directory)"))
        self.data_dir_browse_btn = QPushButton(tr("Browse..."))
        self.data_dir_browse_btn.clicked.connect(self._browse_data_dir)
        data_dir_row.addWidget(self.data_dir_label)
        data_dir_row.addWidget(self.data_dir_edit, stretch=1)
        data_dir_row.addWidget(self.data_dir_browse_btn)

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

        db_layout.addLayout(data_dir_row)
        db_layout.addWidget(self.stats_label)
        db_layout.addLayout(db_btn_row)
        return group

    def _browse_data_dir(self) -> None:
        start_dir = self.data_dir_edit.text().strip()
        selected = QFileDialog.getExistingDirectory(self, tr("Select Index Directory"), start_dir)
        if selected:
            self.data_dir_edit.setText(selected)

    def load_config(self, cfg: AppConfig) -> None:
        """Populate StorageModelTab controls from AppConfig."""
        self.ollama_picker.set_url(cfg.ollama_url)
        self.ollama_picker.set_configured_model(cfg.model)
        self.num_ctx_edit.setText(str(cfg.num_ctx))
        keep_idx = self.keep_alive_combo.findData(cfg.ollama_keep_alive)
        self.keep_alive_combo.setCurrentIndex(max(keep_idx, 0))
        self.timeout_spin.setValue(cfg.ollama_timeout)
        self.api_key_edit.setText(cfg.ollama_api_key)
        self.ollama_picker.set_api_key(cfg.ollama_api_key)
        self.data_dir_edit.setText(cfg.data_dir)
        self.update_database_stats()

    def save_config(self, cfg: AppConfig, validated_ctx: int) -> None:
        """Persist StorageModelTab values into AppConfig."""
        cfg.ollama_url = self.ollama_picker.url()
        selected_model = self.ollama_picker.selected_model()
        if selected_model:
            cfg.model = selected_model
        cfg.num_ctx = validated_ctx
        cfg.ollama_keep_alive = self.keep_alive_combo.currentData()
        cfg.ollama_timeout = self.timeout_spin.value()
        cfg.ollama_api_key = self.api_key_edit.text().strip()
        cfg.data_dir = self.data_dir_edit.text().strip()

    def refresh_theme(self) -> None:
        """Refresh theme-aware status colors in StorageModelTab."""
        self.ollama_picker.refresh_theme()

    def retranslate_ui(self) -> None:
        """Refresh translated labels in StorageModelTab."""
        self.ollama_group.setTitle(tr("Multimodal Embedding Model (Ollama)"))
        self.ollama_picker.retranslate_ui()
        self.num_ctx_label.setText(tr("Context Window (num_ctx):"))
        self.num_ctx_edit.setPlaceholderText(tr("0 (Model default)"))
        self.keep_alive_label.setText(tr("VRAM Policy:"))
        for label, val in KEEP_ALIVE_OPTIONS:
            idx = self.keep_alive_combo.findData(val)
            if idx >= 0:
                self.keep_alive_combo.setItemText(idx, tr(label))
        self.timeout_label.setText(tr("Embedding Timeout (seconds):"))
        self.api_key_label.setText(tr("API Key:"))
        self.api_key_edit.setPlaceholderText(tr("Optional (or OLLAMA_API_KEY)"))
        self.db_group.setTitle(qt_escape_amp(tr("Database && Storage Usage")))
        self.data_dir_label.setText(tr("Index Directory:"))
        self.data_dir_edit.setPlaceholderText(tr("Default (XDG data directory)"))
        self.data_dir_browse_btn.setText(tr("Browse..."))
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
            self.ollama_picker.set_status(
                tr("⚠ Invalid context window: enter a positive integer or 0 for default."),
                tone="warning",
            )
            return None

        value = int(raw)
        if value == 0:
            return 0

        selected_model = self.ollama_picker.selected_model() or DEFAULT_MODEL
        api_key = self.ollama_picker.api_key()
        probe = (
            OllamaClient(base_url=self.ollama_picker.url(), api_key=api_key)
            if api_key
            else OllamaClient(base_url=self.ollama_picker.url())
        )
        with probe:
            max_ctx = probe.get_model_context_length(selected_model)
        if isinstance(max_ctx, int) and max_ctx > 0 and value > max_ctx:
            self.ollama_picker.set_status(
                tr(
                    "⚠ Context window ({num_ctx}) exceeds model maximum ({max_ctx}).",
                    num_ctx=value,
                    max_ctx=max_ctx,
                ),
                tone="warning",
            )
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
    """Tab 4: Global shortcut, search panel behavior, appearance, language, terminal, and autostart."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._status_tone = "muted"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.hotkey_group = self._build_hotkey_group()
        self.lang_group = self._build_appearance_group()
        self.terminal_group = self._build_terminal_group()
        self.autostart_group = self._build_autostart_group()

        layout.addWidget(self.hotkey_group)
        layout.addWidget(self.lang_group)
        layout.addWidget(self.terminal_group)
        layout.addWidget(self.autostart_group)
        layout.addStretch()

    def _build_hotkey_group(self) -> QGroupBox:
        group = QGroupBox(tr("Global Shortcut"))
        hotkey_layout = QFormLayout(group)
        hotkey_layout.setSpacing(6)
        self.hotkey_edit = QLineEdit()
        self.hotkey_edit.setPlaceholderText("Ctrl+Shift+Space")
        self.hotkey_label = QLabel(tr("Quick Search Shortcut:"))
        self.status_label = QLabel("")
        self.status_label.setFont(get_ui_font(11))
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(f"color: {get_palette().text_muted};")
        self.hotkey_status_label = self.status_label
        self.close_on_focus_loss_check = QCheckBox(tr("Hide search panel when clicking outside"))
        self.remember_last_query_check = QCheckBox(tr("Remember last search query on reopen"))
        hotkey_layout.addRow(self.hotkey_label, self.hotkey_edit)
        hotkey_layout.addRow("", self.status_label)
        hotkey_layout.addRow(self.close_on_focus_loss_check)
        hotkey_layout.addRow(self.remember_last_query_check)
        return group

    def _build_appearance_group(self) -> QGroupBox:
        group = QGroupBox(qt_escape_amp(tr("Appearance && Language")))
        lang_layout = QFormLayout(group)
        lang_layout.setSpacing(6)
        self.theme_label = QLabel(tr("Theme:"))
        self.theme_combo = QComboBox()
        for label, val in THEME_OPTIONS:
            self.theme_combo.addItem(tr(label), val)

        self.language_combo = QComboBox()
        self.language_combo.addItem(tr("Auto-detect"), "auto")
        for code, name in SUPPORTED_LANGUAGES.items():
            self.language_combo.addItem(f"{name} ({code})", code)
        self.lang_label = QLabel(tr("Interface Language:"))

        lang_layout.addRow(self.theme_label, self.theme_combo)
        lang_layout.addRow(self.lang_label, self.language_combo)
        return group

    def _build_terminal_group(self) -> QGroupBox:
        group = QGroupBox(tr("Terminal"))
        terminal_layout = QFormLayout(group)
        terminal_layout.setSpacing(6)
        self.terminal_label = QLabel(tr("Preferred Terminal:"))
        self.terminal_combo = QComboBox()
        for label, val in TERMINAL_OPTIONS:
            self.terminal_combo.addItem(tr(label) if val in _TRANSLATABLE_OPTION_VALUES else label, val)
        self.terminal_combo.currentIndexChanged.connect(self._update_custom_terminal_visibility)
        self.custom_terminal_label = QLabel(tr("Custom terminal command:"))
        self.custom_terminal_edit = QLineEdit()
        self.custom_terminal_edit.setPlaceholderText("my-terminal --cwd {dir}")
        terminal_layout.addRow(self.terminal_label, self.terminal_combo)
        terminal_layout.addRow(self.custom_terminal_label, self.custom_terminal_edit)
        self._update_custom_terminal_visibility()
        return group

    def _build_autostart_group(self) -> QGroupBox:
        group = QGroupBox(tr("System Startup"))
        autostart_layout = QVBoxLayout(group)
        self.autostart_check = QCheckBox(tr("Start SemSearch automatically on desktop login"))
        autostart_layout.addWidget(self.autostart_check)
        return group

    def _update_custom_terminal_visibility(self) -> None:
        is_custom = self.terminal_combo.currentData() == "custom"
        self.custom_terminal_label.setVisible(is_custom)
        self.custom_terminal_edit.setVisible(is_custom)

    def _apply_status_tone(self, tone: str) -> None:
        self._status_tone = tone
        palette = get_palette()
        if tone == "success":
            color = palette.leaf
        elif tone == "warning":
            color = palette.amber
        else:
            color = palette.text_muted
        self.status_label.setStyleSheet(f"color: {color};")

    def set_hotkey_error(self, message: str) -> None:
        """Display a hotkey validation error in the warning color."""
        self.status_label.setText(f"⚠ {message}")
        self._apply_status_tone("warning")

    def set_hotkey_status(self, backend_name: str, error: str = "") -> None:
        """Display active global shortcut backend (portal, pynput) or registration error."""
        if backend_name == "portal":
            self.status_label.setText("Active (Wayland XDG GlobalShortcuts portal)")
            self._apply_status_tone("success")
        elif backend_name == "pynput":
            self.status_label.setText("Active (X11 pynput)")
            self._apply_status_tone("success")
        elif error:
            self.status_label.setText(f"⚠ Global shortcut unavailable: {error}")
            self._apply_status_tone("warning")
        else:
            self.status_label.setText("")
            self._apply_status_tone("muted")

    def refresh_theme(self) -> None:
        """Reapply status label color for the active palette."""
        self._apply_status_tone(self._status_tone)

    def load_config(self, cfg: AppConfig) -> None:
        """Populate GeneralTab controls from AppConfig."""
        self.hotkey_edit.setText(cfg.hotkey)
        self.close_on_focus_loss_check.setChecked(cfg.close_on_focus_loss)
        self.remember_last_query_check.setChecked(cfg.remember_last_query)

        theme_idx = self.theme_combo.findData(cfg.theme)
        self.theme_combo.setCurrentIndex(max(theme_idx, 0))

        lang_idx = self.language_combo.findData(cfg.language)
        self.language_combo.setCurrentIndex(max(lang_idx, 0))

        term_idx = self.terminal_combo.findData(cfg.terminal)
        if term_idx >= 0:
            self.terminal_combo.setCurrentIndex(term_idx)
            self.custom_terminal_edit.setText(cfg.custom_terminal_cmd)
        else:
            custom_idx = self.terminal_combo.findData("custom")
            self.terminal_combo.setCurrentIndex(custom_idx)
            self.custom_terminal_edit.setText(cfg.custom_terminal_cmd or cfg.terminal)
        self._update_custom_terminal_visibility()

        self.autostart_check.setChecked(is_autostart_enabled())

    def save_config(self, cfg: AppConfig, hotkey: str) -> None:
        """Persist GeneralTab values into AppConfig and update autostart entry."""
        cfg.hotkey = hotkey
        cfg.close_on_focus_loss = self.close_on_focus_loss_check.isChecked()
        cfg.remember_last_query = self.remember_last_query_check.isChecked()
        selected_theme = self.theme_combo.currentData()
        if selected_theme:
            cfg.theme = selected_theme
        selected_lang = self.language_combo.currentData()
        if selected_lang:
            cfg.language = selected_lang
        cfg.terminal = self.terminal_combo.currentData()
        cfg.custom_terminal_cmd = self.custom_terminal_edit.text().strip()
        set_autostart_enabled(enabled=self.autostart_check.isChecked())

    def retranslate_ui(self) -> None:
        """Refresh translated labels in GeneralTab."""
        self.hotkey_group.setTitle(tr("Global Shortcut"))
        self.hotkey_label.setText(tr("Quick Search Shortcut:"))
        self.close_on_focus_loss_check.setText(tr("Hide search panel when clicking outside"))
        self.remember_last_query_check.setText(tr("Remember last search query on reopen"))
        self.lang_group.setTitle(qt_escape_amp(tr("Appearance && Language")))
        self.theme_label.setText(tr("Theme:"))
        for label, val in THEME_OPTIONS:
            idx = self.theme_combo.findData(val)
            if idx >= 0:
                self.theme_combo.setItemText(idx, tr(label))
        self.lang_label.setText(tr("Interface Language:"))
        auto_idx = self.language_combo.findData("auto")
        if auto_idx >= 0:
            self.language_combo.setItemText(auto_idx, tr("Auto-detect"))
        self.terminal_group.setTitle(tr("Terminal"))
        self.terminal_label.setText(tr("Preferred Terminal:"))
        self.custom_terminal_label.setText(tr("Custom terminal command:"))
        for label, val in TERMINAL_OPTIONS:
            if val in _TRANSLATABLE_OPTION_VALUES:
                idx = self.terminal_combo.findData(val)
                if idx >= 0:
                    self.terminal_combo.setItemText(idx, tr(label))
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
        self.resize(740, 680)

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

        self.refresh_theme(self.config_manager.config.theme)
        self.load_settings()

    def set_hotkey_status(self, backend_name: str, error: str = "") -> None:
        """Update global shortcut backend status displayed in the General tab."""
        self.general_tab.set_hotkey_status(backend_name, error)

    def refresh_theme(self, theme: str) -> None:
        """Apply updated theme to the settings dialog and child status labels."""
        apply_theme(self, theme)
        self.storage_tab.refresh_theme()
        self.general_tab.refresh_theme()

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
        self.folders_tab.load_config(cfg)
        self.code_tab.load_config(cfg)
        self.storage_tab.load_config(cfg)
        self.general_tab.load_config(cfg)

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
            self.general_tab.set_hotkey_error(str(err))
            self.tabs.setCurrentWidget(self.general_tab)
            return

        cfg = self.config_manager.config
        self.folders_tab.save_config(cfg)
        self.code_tab.save_config(cfg)
        self.storage_tab.save_config(cfg, validated_ctx)
        self.general_tab.save_config(cfg, hotkey)

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
