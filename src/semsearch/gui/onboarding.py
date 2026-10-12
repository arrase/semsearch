"""Five-step modal onboarding wizard for initial SemSearch setup."""

from pathlib import Path
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from semsearch.config import ConfigManager
from semsearch.core.ollama import OllamaClient
from semsearch.gui.settings import EDITOR_OPTIONS
from semsearch.gui.theme import ThemeColors, apply_theme, get_ui_font


class WelcomePage(QWizardPage):
    """Step 1: Brand introduction, tagline, and privacy notice."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Welcome to SemSearch")
        self.setSubTitle("Fast, local, keyboard-driven multimodal search for your Linux desktop.")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 20, 16, 20)
        layout.setSpacing(14)

        icon_label = QLabel("⚡")
        icon_label.setFont(get_ui_font(40))

        desc_label = QLabel(
            "SemSearch indexes your documents, screenshots, photos, audio, videos, and source code "
            "using state-of-the-art multimodal embeddings powered by Ollama."
        )
        desc_label.setFont(get_ui_font(13))
        desc_label.setWordWrap(True)

        privacy_label = QLabel(
            "🔒 100% Private: All indexing and vector generation happens completely on your machine. "
            "No data is ever sent to the cloud."
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
    """Step 2: Common folder checkboxes and crawl time estimate."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Select Folders to Index")
        self.setSubTitle("Choose the directories you would like SemSearch to index.")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        home = Path.home()
        self.folder_checkboxes: dict[str, QCheckBox] = {}
        standard_dirs = [
            ("Documents", home / "Documents"),
            ("Pictures", home / "Pictures"),
            ("Desktop", home / "Desktop"),
            ("Downloads", home / "Downloads"),
        ]

        for name, path in standard_dirs:
            cb = QCheckBox(f"{name} ({path})")
            cb.setChecked(name in ("Documents", "Pictures"))
            cb.toggled.connect(self._update_estimate)
            layout.addWidget(cb)
            self.folder_checkboxes[str(path)] = cb

        # Custom folders list
        self.custom_list = QListWidget()
        add_btn = QPushButton("Add Another Folder...")
        add_btn.clicked.connect(self._add_custom_folder)

        layout.addWidget(QLabel("Custom Folders:"))
        layout.addWidget(self.custom_list)
        layout.addWidget(add_btn)

        self.estimate_label = QLabel()
        self.estimate_label.setFont(get_ui_font(11))
        self.estimate_label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED};")
        layout.addWidget(self.estimate_label)

        self._update_estimate()

    def _add_custom_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder:
            self.custom_list.addItem(folder)
            self._update_estimate()

    def _update_estimate(self) -> None:
        selected_count = sum(1 for cb in self.folder_checkboxes.values() if cb.isChecked()) + self.custom_list.count()
        est_seconds = max(10, selected_count * 20)
        self.estimate_label.setText(f"Estimated initial crawl time: ~{est_seconds} seconds")

    def get_selected_folders(self) -> list[str]:
        folders: list[str] = [path for path, cb in self.folder_checkboxes.items() if cb.isChecked()]
        for i in range(self.custom_list.count()):
            folders.append(self.custom_list.item(i).text())
        return folders


class ModelPage(QWizardPage):
    """Step 3: Ollama connection test and verified multimodal model selection."""

    def __init__(self, ollama_client: OllamaClient, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ollama = ollama_client
        self.setTitle("Multimodal Embedding Model")
        self.setSubTitle("SemSearch uses vision-language embeddings for cross-modal search.")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        form = QFormLayout()
        url_row = QHBoxLayout()
        self.url_edit = QLineEdit("http://localhost:11434")
        self.test_btn = QPushButton("Test Connection")
        self.test_btn.clicked.connect(self.test_connection)
        url_row.addWidget(self.url_edit, stretch=1)
        url_row.addWidget(self.test_btn)

        self.model_combo = QComboBox()
        self.model_combo.addItem("embeddinggemma-2")

        form.addRow("Ollama Host URL:", url_row)
        form.addRow("Multimodal Model:", self.model_combo)
        layout.addLayout(form)

        self.status_label = QLabel("Click 'Test Connection' to discover installed multimodal models.")
        self.status_label.setFont(get_ui_font(11))
        self.status_label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED};")
        layout.addWidget(self.status_label)

        tip_label = QLabel("Default model 'embeddinggemma-2' provides high accuracy for text, documents, and visual items.")
        tip_label.setFont(get_ui_font(11))
        tip_label.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY};")
        tip_label.setWordWrap(True)
        layout.addWidget(tip_label)
        layout.addStretch()

    def test_connection(self) -> None:
        """Query Ollama and filter dropdown strictly to verified multimodal models."""
        self.ollama.base_url = self.url_edit.text().strip()
        try:
            models = self.ollama.list_multimodal_models()
            self.model_combo.clear()
            if models:
                for m in models:
                    self.model_combo.addItem(m)
                idx = self.model_combo.findText("embeddinggemma-2")
                if idx >= 0:
                    self.model_combo.setCurrentIndex(idx)
                self.status_label.setText(f"✓ Ollama connected ({len(models)} multimodal models available)")
                self.status_label.setStyleSheet(f"color: {ThemeColors.LEAF};")
            else:
                self.model_combo.addItem("embeddinggemma-2")
                self.status_label.setText("⚠ Ollama connected. Please ensure 'embeddinggemma-2' is pulled.")
                self.status_label.setStyleSheet(f"color: {ThemeColors.AMBER};")
        except Exception as e:
            self.status_label.setText(f"⚠ Connection failed: {e}")
            self.status_label.setStyleSheet(f"color: {ThemeColors.AMBER};")


class CodePage(QWizardPage):
    """Step 4: Source code indexing toggle, repositories, and editor picker."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Developer & Code Search")
        self.setSubTitle("Optional symbol indexing for source code projects.")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.code_enable_check = QCheckBox("Enable Source Code Search")
        self.code_enable_check.setChecked(False)
        self.code_enable_check.toggled.connect(self._on_toggle_code)
        layout.addWidget(self.code_enable_check)

        self.code_container = QWidget()
        code_layout = QVBoxLayout(self.code_container)
        code_layout.setContentsMargins(0, 0, 0, 0)
        code_layout.setSpacing(8)

        self.repos_list = QListWidget()
        add_btn = QPushButton("Add Code Root...")
        add_btn.clicked.connect(self._add_repo)

        editor_row = QHBoxLayout()
        self.editor_combo = QComboBox()
        for label, val in EDITOR_OPTIONS:
            self.editor_combo.addItem(label, val)
        editor_row.addWidget(QLabel("Preferred Editor:"))
        editor_row.addWidget(self.editor_combo, stretch=1)

        code_layout.addWidget(QLabel("Git Repositories / Code Folders:"))
        code_layout.addWidget(self.repos_list)
        code_layout.addWidget(add_btn)
        code_layout.addLayout(editor_row)

        self.code_container.setEnabled(False)
        layout.addWidget(self.code_container)
        layout.addStretch()

    def _on_toggle_code(self, checked: bool) -> None:
        self.code_container.setEnabled(checked)

    def _add_repo(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Code Root Directory")
        if folder:
            self.repos_list.addItem(folder)

    def get_code_folders(self) -> list[str]:
        return [self.repos_list.item(i).text() for i in range(self.repos_list.count())]


class CompletionPage(QWizardPage):
    """Step 5: Setup summary and hotkey instructions."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Setup Complete!")
        self.setSubTitle("You're ready to search your desktop with SemSearch.")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 20, 16, 20)
        layout.setSpacing(14)

        hotkey_box = QWidget()
        hotkey_box.setStyleSheet(
            f"background-color: {ThemeColors.BG_CARD}; border: 1px solid {ThemeColors.BORDER};"
            "border-radius: 8px; padding: 14px;"
        )
        hotkey_layout = QVBoxLayout(hotkey_box)
        hotkey_layout.setSpacing(6)

        title = QLabel("Global Keyboard Shortcut")
        title.setFont(get_ui_font(13, get_ui_font().weight()))

        shortcut_label = QLabel("Press  Ctrl + Shift + Space  anytime to open Quick Search")
        shortcut_label.setFont(get_ui_font(14))
        shortcut_label.setStyleSheet(f"color: {ThemeColors.BRAND_START}; font-weight: bold;")

        hotkey_layout.addWidget(title)
        hotkey_layout.addWidget(shortcut_label)

        tray_tip = QLabel("SemSearch will now run in your system tray and start indexing your selected folders.")
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

        self.setWindowTitle("SemSearch Setup")
        self.resize(580, 460)

        self.welcome_page = WelcomePage()
        self.folders_page = FoldersPage()
        self.model_page = ModelPage(self.ollama)
        self.code_page = CodePage()
        self.completion_page = CompletionPage()

        self.addPage(self.welcome_page)     # 0
        self.addPage(self.folders_page)     # 1
        self.addPage(self.model_page)       # 2
        self.addPage(self.code_page)        # 3
        self.addPage(self.completion_page)  # 4

        apply_theme(self)

    def accept(self) -> None:
        """Persist configuration with onboarded=True and notify caller to start indexing."""
        cfg = self.config_manager.config

        # 1. Folders
        selected_folders = self.folders_page.get_selected_folders()
        if selected_folders:
            cfg.folders = selected_folders

        # 2. Model
        cfg.ollama_url = self.model_page.url_edit.text().strip()
        model_name = self.model_page.model_combo.currentText().strip()
        if model_name:
            cfg.model = model_name

        # 3. Code
        cfg.code_enabled = self.code_page.code_enable_check.isChecked()
        cfg.code_folders = self.code_page.get_code_folders()
        cfg.code_editor = self.code_page.editor_combo.currentData()

        # 4. Mark onboarded
        cfg.onboarded = True
        self.config_manager.save(cfg)

        self.indexing_started.emit()
        super().accept()
