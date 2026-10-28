"""Unit and widget tests for SettingsDialog and multimodal model filtering."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from semsearch.config import ConfigManager
from semsearch.core.database import Database
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
    """Verify autostart desktop file creation with absolute executable path and removal."""
    test_autostart = tmp_path / "autostart" / "semsearch.desktop"
    monkeypatch.setattr("semsearch.gui.settings.AUTOSTART_DIR", test_autostart.parent)
    monkeypatch.setattr("semsearch.gui.settings.AUTOSTART_FILE", test_autostart)
    monkeypatch.setattr("shutil.which", lambda _: "/home/user/.local/bin/semsearch")

    assert not is_autostart_enabled()
    set_autostart_enabled(True)
    assert is_autostart_enabled()
    assert "Exec=/home/user/.local/bin/semsearch --daemon" in test_autostart.read_text(encoding="utf-8")

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


def test_settings_dialog_test_connection_multimodal_filtering(tmp_path: Path, mock_ollama, mock_db, qapp: QApplication):
    """Verify testing connection populates model dropdown strictly with multimodal models."""
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)
    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db)

    # Test connection button click
    dialog.storage_tab.test_btn.click()
    mock_ollama.list_multimodal_models.assert_called_once()

    items = [dialog.storage_tab.model_combo.itemText(i) for i in range(dialog.storage_tab.model_combo.count())]
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


def test_settings_dialog_code_db_stats_aggregation(tmp_path: Path, mock_ollama, qapp: QApplication):
    """Verify update_database_stats aggregates metrics and disk size from both db and code_db."""
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)

    db_file = tmp_path / "index.db"
    db_file.write_bytes(b"x" * 2048)
    mock_db = MagicMock()
    mock_db.path = str(db_file)
    mock_db.stats.return_value = {"files": 100, "segments": 300, "vectors": 250}

    code_db_file = tmp_path / "code.db"
    code_db_file.write_bytes(b"x" * 1024)
    mock_code_db = MagicMock()
    mock_code_db.path = str(code_db_file)
    mock_code_db.stats.return_value = {"files": 50, "segments": 150, "vectors": 120}

    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db, code_db=mock_code_db)
    dialog.storage_tab.update_database_stats()

    label_text = dialog.storage_tab.stats_label.text()
    assert "Indexed Files: 150" in label_text
    assert "Segments: 450" in label_text
    assert "Vectors: 370" in label_text
    assert "3.0 KB" in label_text


def test_settings_dialog_stats_no_databases(tmp_path: Path, mock_ollama, qapp: QApplication):
    """Verify stats label when both db and code_db are None."""
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)

    dialog = SettingsDialog(cfg_mgr, mock_ollama, db=None, code_db=None)
    dialog.storage_tab.update_database_stats()
    assert dialog.storage_tab.stats_label.text() == "Database metrics unavailable"


def test_settings_dialog_vacuum_both_databases(tmp_path: Path, mock_ollama, qapp: QApplication):
    """Verify _vacuum_database executes VACUUM on both db and code_db and refreshes stats."""
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)

    mock_db = MagicMock()
    mock_db.path = str(tmp_path / "index.db")
    mock_db.stats.return_value = {"files": 10, "segments": 20, "vectors": 20}

    mock_code_db = MagicMock()
    mock_code_db.path = str(tmp_path / "code.db")
    mock_code_db.stats.return_value = {"files": 5, "segments": 10, "vectors": 10}

    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db, code_db=mock_code_db)
    dialog.storage_tab.vacuum_btn.click()

    mock_db.vacuum.assert_called_once()
    mock_code_db.vacuum.assert_called_once()


def test_settings_dialog_reindex_requested_signal_forwarding(tmp_path: Path, mock_ollama, mock_db, qapp: QApplication):
    """Verify clicking reindex button emits reindex_requested signal from tab and dialog."""
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)

    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db)
    tab_spy = MagicMock()
    dialog_spy = MagicMock()

    dialog.storage_tab.reindex_requested.connect(tab_spy)
    dialog.reindex_requested.connect(dialog_spy)

    dialog.storage_tab.reindex_btn.click()

    tab_spy.assert_called_once()
    dialog_spy.assert_called_once()


def test_settings_dialog_stats_code_db_only(tmp_path: Path, mock_ollama, qapp: QApplication):
    """Verify update_database_stats works when db is None but code_db is present."""
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)

    code_db_file = tmp_path / "code.db"
    code_db_file.write_bytes(b"x" * 1024)
    mock_code_db = MagicMock()
    mock_code_db.path = str(code_db_file)
    mock_code_db.stats.return_value = {"files": 42, "segments": 84, "vectors": 70}

    dialog = SettingsDialog(cfg_mgr, mock_ollama, db=None, code_db=mock_code_db)
    dialog.storage_tab.update_database_stats()

    label_text = dialog.storage_tab.stats_label.text()
    assert "Indexed Files: 42" in label_text
    assert "Segments: 84" in label_text
    assert "Vectors: 70" in label_text
    assert "1.0 KB" in label_text


def test_settings_dialog_vacuum_db_only(tmp_path: Path, mock_ollama, qapp: QApplication):
    """Verify _vacuum_database executes VACUUM on db when code_db is None without error."""
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)

    mock_db = MagicMock()
    mock_db.path = str(tmp_path / "index.db")
    mock_db.stats.return_value = {"files": 10, "segments": 20, "vectors": 20}

    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db, code_db=None)
    dialog.storage_tab.vacuum_btn.click()

    mock_db.vacuum.assert_called_once()


def test_settings_dialog_vacuum_real_databases(tmp_path: Path, mock_ollama, qapp: QApplication):
    """Verify _vacuum_database executes VACUUM on real SQLite database instances."""
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)

    real_db = Database(tmp_path / "real_index.db")
    real_code_db = Database(tmp_path / "real_code.db")

    dialog = SettingsDialog(cfg_mgr, mock_ollama, real_db, code_db=real_code_db)
    dialog.storage_tab.vacuum_btn.click()

    assert "Indexed Files: 0" in dialog.storage_tab.stats_label.text()
    assert "Segments: 0" in dialog.storage_tab.stats_label.text()
    assert "8.0 KB" in dialog.storage_tab.stats_label.text()


def test_settings_dialog_num_ctx_validation_against_model(tmp_path: Path, mock_ollama, mock_db, qapp: QApplication):
    """Verify context window validation against Ollama model's maximum context_length."""
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)
    mock_ollama.get_model_context_length.return_value = 8192

    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db)
    assert dialog.storage_tab.num_ctx_edit.text() == "0"

    # 1. Invalid non-numeric input is rejected
    dialog.storage_tab.num_ctx_edit.setText("abc")
    dialog.save_settings()
    assert cfg_mgr.config.num_ctx == 0
    assert "Invalid context window" in dialog.storage_tab.status_label.text()

    # 2. Value exceeding model maximum context_length is rejected
    dialog.storage_tab.num_ctx_edit.setText("16384")
    dialog.save_settings()
    assert cfg_mgr.config.num_ctx == 0
    assert "exceeds model maximum (8192)" in dialog.storage_tab.status_label.text()

    # 3. Valid value within model maximum is accepted and saved
    dialog.storage_tab.num_ctx_edit.setText("4096")
    dialog.save_settings()
    assert cfg_mgr.config.num_ctx == 4096
