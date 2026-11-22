"""Reusable widgets and Qt helpers shared across SemSearch GUI surfaces."""

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from semsearch.core.ollama import OllamaClient
from semsearch.gui.theme import get_palette, get_ui_font
from semsearch.i18n import tr

DEFAULT_MODEL = "embeddinggemma-2"


def qt_escape_amp(text: str) -> str:
    """Escape '&' as '&&' for Qt titles (QGroupBox, QTabWidget) that interpret '&' as a mnemonic."""
    return text.replace("&&", "&").replace("&", "&&")


class ElidedLabel(QLabel):
    """Single-line label that elides overflowing text with an ellipsis and shows the full text as tooltip."""

    def __init__(
        self,
        text: str = "",
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._mode = mode
        self._full_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._elide()

    def text(self) -> str:
        return self._full_text

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        super().setText(self.fontMetrics().elidedText(self._full_text, self._mode, self.contentsRect().width()))


class FolderListEditor(QWidget):
    """Reusable directory list widget with deduplicating Add and Remove buttons."""

    folders_changed = Signal()

    def __init__(
        self,
        add_label: str,
        remove_label: str,
        dialog_title: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._dialog_title = dialog_title

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.list_widget = QListWidget()

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton(add_label)
        self.add_btn.clicked.connect(self._on_add_clicked)
        self.remove_btn = QPushButton(remove_label)
        self.remove_btn.clicked.connect(self._on_remove_clicked)
        btn_row.addWidget(self.add_btn)
        btn_row.addWidget(self.remove_btn)
        btn_row.addStretch()

        layout.addWidget(self.list_widget, stretch=1)
        layout.addLayout(btn_row)

    def set_folders(self, folders: list[str]) -> None:
        """Replace list entries with deduplicated folder paths."""
        self.list_widget.clear()
        seen: set[str] = set()
        for folder in folders:
            cleaned = folder.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                self.list_widget.addItem(cleaned)
        self.folders_changed.emit()

    def get_folders(self) -> list[str]:
        """Return ordered folder paths currently in the list."""
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count())]

    def add_folder(self, folder: str) -> bool:
        """Add a folder path if non-empty and not already present."""
        cleaned = folder.strip()
        if not cleaned or cleaned in self.get_folders():
            return False
        self.list_widget.addItem(cleaned)
        self.folders_changed.emit()
        return True

    def retranslate_ui(self, add_label: str, remove_label: str, dialog_title: str) -> None:
        """Update localized button labels and folder picker title."""
        self.add_btn.setText(add_label)
        self.remove_btn.setText(remove_label)
        self._dialog_title = dialog_title

    def _on_add_clicked(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, self._dialog_title)
        if folder:
            self.add_folder(folder)

    def _on_remove_clicked(self) -> None:
        row = self.list_widget.currentRow()
        if row >= 0:
            self.list_widget.takeItem(row)
            self.folders_changed.emit()


class OllamaConnectionWorker(QThread):
    """Background thread probing an Ollama endpoint using a short-lived OllamaClient."""

    succeeded = Signal(list)
    failed = Signal(str)

    def __init__(self, base_url: str, *, api_key: str = "", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.base_url = base_url
        self.api_key = api_key.strip()

    def run(self) -> None:
        try:
            client = (
                OllamaClient(base_url=self.base_url, api_key=self.api_key)
                if self.api_key
                else OllamaClient(base_url=self.base_url)
            )
            with client:
                models = client.list_multimodal_models()
        except Exception as exc:  # noqa: BLE001 — GUI probe boundary formats error text without crashing UI
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(models)


class OllamaModelPicker(QWidget):
    """Reusable Ollama URL input, async connection tester, and verified multimodal model selector."""

    connection_succeeded = Signal(list)
    connection_failed = Signal(str)

    def __init__(self, default_url: str = "http://localhost:11434", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._configured_model = DEFAULT_MODEL
        self._api_key = ""
        self._status_tone = "muted"
        self._worker: OllamaConnectionWorker | None = None
        self._active_workers: set[OllamaConnectionWorker] = set()

        self.form_layout = QFormLayout(self)
        self.form_layout.setContentsMargins(0, 0, 0, 0)
        self.form_layout.setSpacing(8)

        url_row = QHBoxLayout()
        self.url_edit = QLineEdit(default_url)
        self.test_btn = QPushButton(tr("Test Connection"))
        self.test_btn.clicked.connect(self.test_connection_async)
        url_row.addWidget(self.url_edit, stretch=1)
        url_row.addWidget(self.test_btn)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(False)

        self.status_label = QLabel()
        self.status_label.setFont(get_ui_font(11))
        self.status_label.setWordWrap(True)

        self.host_url_label = QLabel(tr("Ollama Host URL:"))
        self.model_label = QLabel(tr("Multimodal Model:"))

        self.form_layout.addRow(self.host_url_label, url_row)
        self.form_layout.addRow(self.model_label, self.model_combo)
        self.form_layout.addRow("", self.status_label)

    def insert_row(self, label: QWidget, field: QWidget) -> None:
        """Insert an additional form row directly above the connection status label."""
        self.form_layout.insertRow(self.form_layout.rowCount() - 1, label, field)

    def set_url(self, url: str) -> None:
        """Set the Ollama host URL text field."""
        self.url_edit.setText(url)

    def url(self) -> str:
        """Return trimmed Ollama host URL."""
        return self.url_edit.text().strip()

    def set_api_key(self, api_key: str) -> None:
        """Set optional Ollama API key used for connection probes."""
        self._api_key = api_key.strip()

    def api_key(self) -> str:
        """Return trimmed Ollama API key."""
        return self._api_key

    def selected_model(self) -> str:
        """Return currently selected model name."""
        return self.model_combo.currentText().strip()

    @staticmethod
    def _tone_color(tone: str) -> str:
        palette = get_palette()
        if tone == "success":
            return palette.leaf
        if tone == "warning":
            return palette.amber
        return palette.text_muted

    def set_status(self, text: str, *, tone: str = "muted") -> None:
        """Update status text and theme-aware status color."""
        self._status_tone = tone
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {self._tone_color(tone)};")

    def refresh_theme(self) -> None:
        """Reapply status label color for the active palette."""
        self.status_label.setStyleSheet(f"color: {self._tone_color(self._status_tone)};")

    def set_configured_model(self, model: str, *, hint_text: str = "") -> None:
        """Populate model dropdown with persisted model without claiming an active connection."""
        target = model.strip() or DEFAULT_MODEL
        self._configured_model = target
        self.model_combo.clear()
        self.model_combo.addItem(target)
        self.set_status(hint_text, tone="muted")

    def populate_multimodal_models(self, models: list[str]) -> None:
        """Populate verified multimodal models while preserving the user's selected/configured model."""
        preferred = self.model_combo.currentText().strip() or self._configured_model
        self.model_combo.clear()
        if models:
            for model_name in models:
                self.model_combo.addItem(model_name)
            preferred_idx = self.model_combo.findText(preferred)
            if preferred_idx >= 0:
                self.model_combo.setCurrentIndex(preferred_idx)
            else:
                default_idx = self.model_combo.findText(DEFAULT_MODEL)
                if default_idx >= 0:
                    self.model_combo.setCurrentIndex(default_idx)
            self._configured_model = self.model_combo.currentText()
            self.set_status(
                tr("✓ Connected: {count} multimodal model(s) verified.", count=len(models)),
                tone="success",
            )
        else:
            self.model_combo.addItem(preferred or DEFAULT_MODEL)
            self.set_status(
                tr("⚠ No multimodal models detected on Ollama host. Run 'ollama pull embeddinggemma-2'."),
                tone="warning",
            )

    def test_connection_async(self) -> None:
        """Probe the entered Ollama URL on a background QThread without mutating shared clients."""
        if self._worker is not None and self._worker.isRunning():
            return
        self.test_btn.setEnabled(False)
        worker = OllamaConnectionWorker(self.url(), api_key=self._api_key, parent=self)
        self._worker = worker
        self._active_workers.add(worker)
        worker.succeeded.connect(self._apply_connection_success)
        worker.failed.connect(self._apply_connection_error)
        worker.finished.connect(lambda w=worker: self._cleanup_worker(w))
        worker.start()

    def test_connection(self) -> list[str] | None:
        """Synchronously probe the entered Ollama URL using a temporary closed OllamaClient."""
        url = self.url()
        key = self._api_key
        try:
            client = OllamaClient(base_url=url, api_key=key) if key else OllamaClient(base_url=url)
            with client:
                models = client.list_multimodal_models()
        except Exception as exc:  # noqa: BLE001 — GUI probe boundary formats error text without crashing UI
            self._apply_connection_error(str(exc))
            return None
        self._apply_connection_success(models)
        return models

    def _apply_connection_success(self, models: list[str]) -> None:
        self.populate_multimodal_models(models)
        self.connection_succeeded.emit(models)

    def _apply_connection_error(self, error: str) -> None:
        self.set_status(tr("⚠ Connection failed: {error}", error=error), tone="warning")
        self.connection_failed.emit(error)

    def _cleanup_worker(self, worker: OllamaConnectionWorker) -> None:
        self._active_workers.discard(worker)
        if self._worker is worker:
            self._worker = None
            self.test_btn.setEnabled(True)
        worker.deleteLater()

    def retranslate_ui(self) -> None:
        """Refresh translated labels for Ollama picker controls."""
        self.test_btn.setText(tr("Test Connection"))
        self.host_url_label.setText(tr("Ollama Host URL:"))
        self.model_label.setText(tr("Multimodal Model:"))

    def shutdown(self) -> None:
        """Wait for any running background connection probe threads to finish."""
        workers = self._active_workers.copy()
        self._active_workers.clear()
        for worker in workers:
            worker.wait(2000)
        self._worker = None
        self.test_btn.setEnabled(True)
