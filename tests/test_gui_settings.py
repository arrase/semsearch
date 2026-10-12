"""Unit and widget tests for SettingsDialog and multimodal model filtering."""

from pathlib import Path
from unittest.mock import MagicMock
import pytest
from PySide6.QtWidgets import QApplication

from semsearch.config import ConfigManager
from semsearch.gui.settings import (
    SettingsDialog,
    is_autostart_enabled,
    set_autostart_enabled,
)


@pytest.fixture
def mock_ollama():
    client = MagicMock()
    client.list_multimodal_models.return_value = ["embeddinggemma-2", "llava"]
    return client


@pytest.fixture
def mock_db(tmp_path: Path):
    db = MagicMock()
    db_file = tmp_path / "index.db"
    db_file.write_text("dummy", encoding="utf-8")
    db.path = str(db_file)
    db.db_path = str(db_file)
    db.stats.return_value = {"files": 120, "segments": 450, "vectors": 400}
    return db


def test_autostart_toggle(tmp_path: Path, monkeypatch):
    """Verify autostart desktop file creation and removal."""
    test_autostart = tmp_path / "autostart" / "semsearch.desktop"
    monkeypatch.setattr("semsearch.gui.settings.AUTOSTART_DIR", test_autostart.parent)
    monkeypatch.setattr("semsearch.gui.settings.AUTOSTART_FILE", test_autostart)

    assert not is_autostart_enabled()
    set_autostart_enabled(True)
    assert is_autostart_enabled()
    assert "Exec=semsearch --daemon" in test_autostart.read_text(encoding="utf-8")

    set_autostart_enabled(False)
    assert not is_autostart_enabled()


def test_settings_dialog_load_and_tabs(tmp_path: Path, mock_ollama, mock_db, qapp: QApplication):
    """Verify settings loaded into dialog tabs."""
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)
    cfg_mgr.config.folders = ["/home/user/Documents"]
    cfg_mgr.config.code_enabled = True
    cfg_mgr.config.code_editor = "zed"
    cfg_mgr.config.model = "embeddinggemma-2"

    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db)

    # Check tab 1
    assert dialog.folders_tab.folders_list.count() == 1
    assert dialog.folders_tab.folders_list.item(0).text() == "/home/user/Documents"

    # Check tab 2
    assert dialog.code_tab.code_enable_check.isChecked()
    assert dialog.code_tab.editor_combo.currentData() == "zed"

    # Check tab 3
    assert dialog.storage_tab.model_combo.currentText() == "embeddinggemma-2"
    assert "120" in dialog.storage_tab.stats_label.text()

    # Check tab 4
    assert dialog.general_tab.hotkey_edit.text() == "ctrl+shift+space"


def test_settings_dialog_test_connection_multimodal_filtering(
    tmp_path: Path, mock_ollama, mock_db, qapp: QApplication
):
    """Verify testing connection populates model dropdown strictly with multimodal models."""
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)
    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db)

    # Test connection button click
    dialog.storage_tab.test_btn.click()
    mock_ollama.list_multimodal_models.assert_called_once()

    items = [
        dialog.storage_tab.model_combo.itemText(i)
        for i in range(dialog.storage_tab.model_combo.count())
    ]
    assert items == ["embeddinggemma-2", "llava"]
    assert dialog.storage_tab.model_combo.currentText() == "embeddinggemma-2"


def test_settings_dialog_save_settings(tmp_path: Path, mock_ollama, mock_db, qapp: QApplication):
    """Verify changes in dialog are saved back to ConfigManager."""
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)
    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db)

    # Modify values
    dialog.folders_tab.folders_list.addItem("/home/user/Pictures")
    dialog.folders_tab.skipped_folders_edit.setText("node_modules, target")
    dialog.folders_tab.skipped_ext_edit.setText("heic, raw")
    dialog.code_tab.code_enable_check.setChecked(True)
    idx = dialog.code_tab.editor_combo.findData("cursor")
    dialog.code_tab.editor_combo.setCurrentIndex(idx)
    dialog.general_tab.hotkey_edit.setText("ctrl+alt+space")

    dialog.save_settings()

    saved = cfg_mgr.config
    assert "/home/user/Pictures" in saved.folders
    assert "node_modules" in saved.excluded_folders
    assert "heic" in saved.excluded_types
    assert saved.code_enabled is True
    assert saved.code_editor == "cursor"
    assert saved.hotkey == "ctrl+alt+space"


def test_settings_dialog_test_connection_error_handling(tmp_path: Path, mock_db, qapp: QApplication):
    """Verify Ollama connection error is handled gracefully in status label."""
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)
    mock_ollama = MagicMock()
    mock_ollama.list_multimodal_models.side_effect = ConnectionError("Connection refused")

    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db)
    dialog.storage_tab.test_connection()
    assert "Connection failed" in dialog.storage_tab.status_label.text()
