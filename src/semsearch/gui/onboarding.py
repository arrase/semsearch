"""Five-step modal onboarding wizard for initial SemSearch setup."""

from pathlib import Path

from PySide6.QtCore import QStandardPaths, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from semsearch.config import ConfigManager
from semsearch.core.ollama import OllamaClient
from semsearch.gui.settings import EDITOR_OPTIONS
from semsearch.gui.theme import ThemeColors, apply_theme, get_ui_font
from semsearch.gui.widgets import DEFAULT_MODEL, FolderListEditor, OllamaModelPicker
from semsearch.i18n import tr

_STANDARD_LOCATIONS: tuple[tuple[str, QStandardPaths.StandardLocation, bool], ...] = (
    ("Documents", QStandardPaths.StandardLocation.DocumentsLocation, True),
    ("Pictures", QStandardPaths.StandardLocation.PicturesLocation, True),
    ("Desktop", QStandardPaths.StandardLocation.DesktopLocation, False),
    ("Downloads", QStandardPaths.StandardLocation.DownloadLocation, False),
)


def discover_standard_folders() -> list[tuple[str, Path, bool]]:
    """Discover existing XDG standard directories via QStandardPaths with deduplication."""
    home = Path.home().resolve()
    seen: set[Path] = set()
    discovered: list[tuple[str, Path, bool]] = []
    for name, location, default_checked in _STANDARD_LOCATIONS:
        raw = QStandardPaths.writableLocation(location)
        candidate = Path(raw) if raw else (home / name)
        if not candidate.is_dir():
            continue
        resolved = candidate.resolve()
        if resolved == home or resolved in seen:
            continue
        seen.add(resolved)
        discovered.append((name, resolved, default_checked))
    return discovered


class WelcomePage(QWizardPage):
    """Step 1: Brand introduction, tagline, and privacy notice."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle(tr("Welcome to SemSearch"))
        self.setSubTitle(tr("Fast, local, keyboard-driven multimodal search for your Linux desktop."))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 20, 16, 20)
        layout.setSpacing(14)

        icon_label = QLabel("⚡")
        icon_label.setFont(get_ui_font(40))

        desc_label = QLabel(
            tr(
                "SemSearch indexes your documents, screenshots, photos, audio, videos, and source code "
                "using state-of-the-art multimodal embeddings powered by Ollama."
            )
        )
        desc_label.setFont(get_ui_font(13))
        desc_label.setWordWrap(True)

        privacy_label = QLabel(
            tr(
                "🔒 100% Private: All indexing and vector generation happens completely on your machine. "
                "No data is ever sent to the cloud."
            )
        )
        privacy_label.setFont(get_ui_font(12))
        privacy_label.setStyleSheet(
            f"background-color: {ThemeColors.BG_CARD}; border: 1px solid {ThemeColors.BORDER};"
            "border-radius: 8px; padding: 12px;"
        )
        privacy_label.setWordWrap(True)

        layout.addWidget(icon_label)
        layout.addWidget(desc_label)
        layout.addWidget(privacy_label)
        layout.addStretch()


class FoldersPage(QWizardPage):
    """Step 2: XDG standard folder checkboxes, custom folders, and crawl time estimate."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle(tr("Select Folders to Index"))
        self.setSubTitle(tr("Choose the directories you would like SemSearch to index."))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.folder_checkboxes: dict[str, QCheckBox] = {}
        for name, path, default_checked in discover_standard_folders():
            cb = QCheckBox(f"{tr(name)} ({path})")
            cb.setChecked(default_checked)
            cb.toggled.connect(self._update_estimate)
            layout.addWidget(cb)
            self.folder_checkboxes[str(path)] = cb

        self.custom_editor = FolderListEditor(
            tr("Add Another Folder..."),
            tr("Remove Selected"),
            tr("Select Folder"),
        )
        self.custom_list = self.custom_editor.list_widget
        self.custom_editor.folders_changed.connect(self._update_estimate)

        layout.addWidget(QLabel(tr("Custom Folders:")))
        layout.addWidget(self.custom_editor)

        self.estimate_label = QLabel()
        self.estimate_label.setFont(get_ui_font(11))
        self.estimate_label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED};")
        layout.addWidget(self.estimate_label)

        self._update_estimate()

    def _update_estimate(self) -> None:
        selected_count = sum(1 for cb in self.folder_checkboxes.values() if cb.isChecked()) + self.custom_list.count()
        est_seconds = max(10, selected_count * 20)
        self.estimate_label.setText(tr("Estimated initial crawl time: ~{seconds} seconds", seconds=est_seconds))

    def get_selected_folders(self) -> list[str]:
        """Return deduplicated list of checked standard folders and custom folders."""
        folders = [path for path, cb in self.folder_checkboxes.items() if cb.isChecked()]
        folders.extend(self.custom_editor.get_folders())
        return list(dict.fromkeys(folders))


class ModelPage(QWizardPage):
    """Step 3: Ollama connection test and verified multimodal model selection."""

    def __init__(
        self,
        ollama_client: OllamaClient,
        initial_url: str = "http://localhost:11434",
        initial_model: str = DEFAULT_MODEL,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.ollama = ollama_client
        self.setTitle(tr("Multimodal Embedding Model"))
        self.setSubTitle(tr("SemSearch uses vision-language embeddings for cross-modal search."))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.ollama_picker = OllamaModelPicker(default_url=initial_url)
        self.ollama_picker.set_configured_model(
            initial_model,
            hint_text=tr("Click 'Test Connection' to discover installed multimodal models."),
        )
        self.url_edit = self.ollama_picker.url_edit
        self.test_btn = self.ollama_picker.test_btn
        self.model_combo = self.ollama_picker.model_combo
        self.status_label = self.ollama_picker.status_label

        layout.addWidget(self.ollama_picker)

        tip_label = QLabel(
            tr("Default model 'embeddinggemma-2' provides high accuracy for text, documents, and visual items.")
        )
        tip_label.setFont(get_ui_font(11))
        tip_label.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY};")
        tip_label.setWordWrap(True)
        layout.addWidget(tip_label)
        layout.addStretch()

    def test_connection(self) -> None:
        """Probe Ollama host without mutating the shared OllamaClient."""
        self.ollama_picker.test_connection()


class CodePage(QWizardPage):
    """Step 4: Source code indexing toggle, repositories, and editor picker."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle(tr("Developer && Code Search").replace("&&", "&"))
        self.setSubTitle(tr("Optional symbol indexing for source code projects."))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.code_enable_check = QCheckBox(tr("Enable Source Code Search"))
        self.code_enable_check.setChecked(False)
        self.code_enable_check.toggled.connect(self._on_toggle_code)
        layout.addWidget(self.code_enable_check)

        self.code_container = QWidget()
        code_layout = QVBoxLayout(self.code_container)
        code_layout.setContentsMargins(0, 0, 0, 0)
        code_layout.setSpacing(8)

        self.repos_editor = FolderListEditor(
            tr("Add Code Root..."),
            tr("Remove Selected"),
            tr("Select Code Root Directory"),
        )
        self.repos_list = self.repos_editor.list_widget

        editor_row = QHBoxLayout()
        self.editor_combo = QComboBox()
        for label, val in EDITOR_OPTIONS:
            self.editor_combo.addItem(tr(label) if val == "auto" else label, val)
        editor_row.addWidget(QLabel(tr("Preferred Editor:")))
        editor_row.addWidget(self.editor_combo, stretch=1)

        code_layout.addWidget(QLabel(tr("Git Repositories / Code Folders:")))
        code_layout.addWidget(self.repos_editor)
        code_layout.addLayout(editor_row)

        self.code_container.setEnabled(False)
        layout.addWidget(self.code_container)
        layout.addStretch()

    def _on_toggle_code(self) -> None:
        self.code_container.setEnabled(self.code_enable_check.isChecked())

    def get_code_folders(self) -> list[str]:
        """Return configured code root directories."""
        return self.repos_editor.get_folders()


class CompletionPage(QWizardPage):
    """Step 5: Setup summary and hotkey instructions."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle(tr("Setup Complete!"))
        self.setSubTitle(tr("You're ready to search your desktop with SemSearch."))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 20, 16, 20)
        layout.setSpacing(14)

        hotkey_box = QFrame()
        hotkey_box.setObjectName("hotkeyCard")
        hotkey_box.setStyleSheet(
            f"QFrame#hotkeyCard {{ background-color: {ThemeColors.BG_CARD}; "
            f"border: 1px solid {ThemeColors.BORDER}; border-radius: 8px; }}"
        )
        hotkey_layout = QVBoxLayout(hotkey_box)
        hotkey_layout.setContentsMargins(14, 14, 14, 14)
        hotkey_layout.setSpacing(6)

        title = QLabel(tr("Global Keyboard Shortcut"))
        title.setFont(get_ui_font(13))
        title.setStyleSheet("border: none; background: transparent;")

        shortcut_label = QLabel(tr("Press  Ctrl + Shift + Space  anytime to open Quick Search"))
        shortcut_label.setFont(get_ui_font(14))
        shortcut_label.setStyleSheet(
            f"color: {ThemeColors.BRAND_START}; font-weight: bold; border: none; background: transparent;"
        )

        hotkey_layout.addWidget(title)
        hotkey_layout.addWidget(shortcut_label)

        tray_tip = QLabel(tr("SemSearch will now run in your system tray and start indexing your selected folders."))
        tray_tip.setFont(get_ui_font(12))
        tray_tip.setWordWrap(True)

        layout.addWidget(hotkey_box)
        layout.addWidget(tray_tip)
        layout.addStretch()


class OnboardingWizard(QWizard):
    """Modal 5-step onboarding wizard for first-run configuration."""

    indexing_started = Signal()

    def __init__(
        self,
        config_manager: ConfigManager,
        ollama_client: OllamaClient,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config_manager = config_manager
        self.ollama = ollama_client
        cfg = self.config_manager.config

        self.setWindowTitle(tr("SemSearch Setup"))
        self.resize(580, 460)

        self.welcome_page = WelcomePage()
        self.folders_page = FoldersPage()
        self.model_page = ModelPage(self.ollama, initial_url=cfg.ollama_url, initial_model=cfg.model)
        self.code_page = CodePage()
        self.completion_page = CompletionPage()

        self.addPage(self.welcome_page)  # 0
        self.addPage(self.folders_page)  # 1
        self.addPage(self.model_page)  # 2
        self.addPage(self.code_page)  # 3
        self.addPage(self.completion_page)  # 4

        apply_theme(self)

    def accept(self) -> None:
        """Persist configuration with onboarded=True and notify caller to start indexing."""
        cfg = self.config_manager.config

        selected_folders = self.folders_page.get_selected_folders()
        if selected_folders:
            cfg.folders = selected_folders

        cfg.ollama_url = self.model_page.ollama_picker.url()
        model_name = self.model_page.ollama_picker.selected_model()
        if model_name:
            cfg.model = model_name
        self.ollama.base_url = cfg.ollama_url

        cfg.code_enabled = self.code_page.code_enable_check.isChecked()
        cfg.code_folders = self.code_page.get_code_folders()
        cfg.code_editor = self.code_page.editor_combo.currentData()

        cfg.onboarded = True
        self.config_manager.save()

        self.indexing_started.emit()
        super().accept()

    def done(self, result: int) -> None:
        self.model_page.ollama_picker.shutdown()
        super().done(result)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.model_page.ollama_picker.shutdown()
        super().closeEvent(event)
