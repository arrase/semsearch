"""Unit and widget tests for SettingsDialog, async Ollama probe/vacuum, and hotkey validation."""

import threading
import time
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, create_autospec

import pytest
from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication, QLineEdit

from semsearch.config import ConfigManager
from semsearch.core.database import Database
from semsearch.core.ollama import OllamaClient
from semsearch.gui.settings import (
    SettingsDialog,
    is_autostart_enabled,
    set_autostart_enabled,
)


def _process_until(qapp: QApplication, predicate: object, timeout_ms: int = 2000) -> bool:
    assert callable(predicate)
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        qapp.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        if predicate():
            return True
        time.sleep(0.01)
    qapp.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
    return bool(predicate())


class _FakeProbeClient:
    """Short-lived fake OllamaClient tracking base_url, calls, and deterministic close()."""

    def __init__(
        self,
        base_url: str,
        *,
        models: list[str] | None = None,
        error: Exception | None = None,
        max_ctx: int | None = 8192,
    ) -> None:
        self.base_url = base_url
        self._models = models if models is not None else ["embeddinggemma-2", "vision-embed"]
        self._error = error
        self._max_ctx = max_ctx
        self.closed = False

    def __enter__(self) -> "_FakeProbeClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self.closed = True

    def list_multimodal_models(self) -> list[str]:
        if self._error is not None:
            raise self._error
        return list(self._models)

    def get_model_context_length(self, _model: str) -> int | None:
        return self._max_ctx


@pytest.fixture
def mock_ollama() -> MagicMock:
    client = cast(MagicMock, create_autospec(OllamaClient, instance=True))
    client.base_url = "http://localhost:11434"
    return client


@pytest.fixture
def mock_db(tmp_path: Path) -> MagicMock:
    db = cast(MagicMock, create_autospec(Database, instance=True))
    db_file = tmp_path / "index.db"
    db_file.write_text("dummy", encoding="utf-8")
    db.path = str(db_file)
    db.stats.return_value = {"files": 120, "segments": 450, "vectors": 400}
    return db


def test_autostart_toggle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify autostart desktop file quotes Exec path, includes Icon=semsearch, and removes cleanly."""
    test_autostart = tmp_path / "autostart" / "semsearch.desktop"
    monkeypatch.setattr("semsearch.gui.settings.AUTOSTART_DIR", test_autostart.parent)
    monkeypatch.setattr("semsearch.gui.settings.AUTOSTART_FILE", test_autostart)
    monkeypatch.setattr("shutil.which", lambda _: "/home/user/.local/bin/semsearch")

    assert not is_autostart_enabled()
    set_autostart_enabled(enabled=True)
    assert is_autostart_enabled()
    content = test_autostart.read_text(encoding="utf-8")
    assert 'Exec="/home/user/.local/bin/semsearch" --daemon' in content
    assert "Icon=semsearch" in content

    set_autostart_enabled(enabled=False)
    assert not is_autostart_enabled()


def test_settings_dialog_load_no_fake_connected_and_preserves_model(
    tmp_path: Path, mock_ollama: MagicMock, mock_db: MagicMock, qapp: QApplication
) -> None:
    """Verify opening SettingsDialog loads tabs without fake Connected badge and preserves saved model."""
    del qapp
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)
    cfg_mgr.config.folders = ["/home/user/Documents"]
    cfg_mgr.config.code_enabled = True
    cfg_mgr.config.code_editor = "zed"
    cfg_mgr.config.model = "custom-multimodal:latest"

    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db)

    assert dialog.folders_tab.folders_list.count() == 1
    assert dialog.folders_tab.folders_list.item(0).text() == "/home/user/Documents"
    assert dialog.code_tab.code_enable_check.isChecked()
    assert dialog.code_tab.editor_combo.currentData() == "zed"
    assert dialog.storage_tab.model_combo.currentText() == "custom-multimodal:latest"
    assert "Connected" not in dialog.storage_tab.status_label.text()
    assert "120" in dialog.storage_tab.stats_label.text()
    assert dialog.general_tab.hotkey_edit.text() == "ctrl+shift+space"
    dialog.shutdown()


def test_settings_dialog_probe_does_not_mutate_shared_ollama_and_preserves_selected_model(
    tmp_path: Path,
    mock_ollama: MagicMock,
    mock_db: MagicMock,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify async/sync connection probe uses a closed temporary OllamaClient and keeps configured model."""
    created_probes: list[_FakeProbeClient] = []

    def make_probe(*, base_url: str) -> _FakeProbeClient:
        probe = _FakeProbeClient(base_url, models=["embeddinggemma-2", "custom-vision:v2"])
        created_probes.append(probe)
        return probe

    monkeypatch.setattr("semsearch.gui.widgets.OllamaClient", make_probe)
    monkeypatch.setattr("semsearch.gui.settings.OllamaClient", make_probe)

    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)
    cfg_mgr.config.model = "custom-vision:v2"
    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db)

    dialog.storage_tab.url_edit.setText("http://staging-ollama:11434")
    dialog.storage_tab.test_btn.click()
    assert _process_until(qapp, lambda: "Connected" in dialog.storage_tab.status_label.text())

    items = [dialog.storage_tab.model_combo.itemText(i) for i in range(dialog.storage_tab.model_combo.count())]
    assert items == ["embeddinggemma-2", "custom-vision:v2"]
    assert dialog.storage_tab.model_combo.currentText() == "custom-vision:v2"
    assert len(created_probes) == 1
    assert created_probes[0].base_url == "http://staging-ollama:11434"
    assert created_probes[0].closed is True

    # Cancelling dialog leaves shared OllamaClient.base_url untouched
    dialog.reject()
    assert mock_ollama.base_url == "http://localhost:11434"


def test_settings_dialog_save_settings_and_hotkey_validation(
    tmp_path: Path, mock_ollama: MagicMock, mock_db: MagicMock, qapp: QApplication
) -> None:
    """Verify hotkey validation blocks malformed shortcuts and valid settings persist cleanly."""
    del qapp
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)
    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db)

    # 1. Invalid hotkey rejected with status warning and switches to General tab
    dialog.general_tab.hotkey_edit.setText("space")
    dialog.save_settings()
    assert dialog.tabs.currentWidget() is dialog.general_tab
    assert "⚠" in dialog.general_tab.status_label.text()
    assert cfg_mgr.config.hotkey == "ctrl+shift+space"

    # 2. Valid changes persist to ConfigManager
    dialog.folders_tab.folders_editor.add_folder("/home/user/Pictures")
    dialog.folders_tab.folders_editor.add_folder("/home/user/Pictures")  # deduplicated
    dialog.folders_tab.skipped_folders_edit.setText("node_modules, target")
    dialog.folders_tab.skipped_ext_edit.setText("heic, raw")
    dialog.code_tab.code_enable_check.setChecked(True)
    idx = dialog.code_tab.editor_combo.findData("cursor")
    dialog.code_tab.editor_combo.setCurrentIndex(idx)
    dialog.general_tab.hotkey_edit.setText("super+space")

    dialog.save_settings()

    saved = cfg_mgr.config
    assert saved.folders.count("/home/user/Pictures") == 1
    assert "node_modules" in saved.excluded_folders
    assert "heic" in saved.excluded_types
    assert saved.code_enabled is True
    assert saved.code_editor == "cursor"
    assert saved.hotkey == "super+space"


def test_settings_dialog_hotkey_status_feedback(
    tmp_path: Path, mock_ollama: MagicMock, mock_db: MagicMock, qapp: QApplication
) -> None:
    """Verify set_hotkey_status reports portal, pynput, and registration errors in GeneralTab."""
    del qapp
    cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")
    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db)

    dialog.set_hotkey_status("portal")
    assert "Wayland" in dialog.general_tab.status_label.text()

    dialog.set_hotkey_status("pynput")
    assert "pynput" in dialog.general_tab.status_label.text()

    dialog.set_hotkey_status("none", "Portal unavailable")
    assert "Portal unavailable" in dialog.general_tab.status_label.text()
    dialog.shutdown()


def test_settings_dialog_test_connection_error_handling(
    tmp_path: Path,
    mock_ollama: MagicMock,
    mock_db: MagicMock,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Ollama connection error is formatted cleanly and temporary client is closed."""
    del qapp
    created: list[_FakeProbeClient] = []

    def failing_probe(*, base_url: str) -> _FakeProbeClient:
        probe = _FakeProbeClient(base_url, error=ConnectionError("Connection refused"))
        created.append(probe)
        return probe

    monkeypatch.setattr("semsearch.gui.widgets.OllamaClient", failing_probe)

    cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")
    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db)
    dialog.storage_tab.test_connection()
    assert "Connection failed" in dialog.storage_tab.status_label.text()
    assert created
    assert created[0].closed is True
    dialog.shutdown()


def test_settings_dialog_code_db_stats_aggregation(tmp_path: Path, mock_ollama: MagicMock, qapp: QApplication) -> None:
    """Verify update_database_stats aggregates metrics and disk size from both db and code_db."""
    del qapp
    cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")

    db_file = tmp_path / "index.db"
    db_file.write_bytes(b"x" * 2048)
    mock_db = create_autospec(Database, instance=True)
    mock_db.path = str(db_file)
    mock_db.stats.return_value = {"files": 100, "segments": 300, "vectors": 250}

    code_db_file = tmp_path / "code.db"
    code_db_file.write_bytes(b"x" * 1024)
    mock_code_db = create_autospec(Database, instance=True)
    mock_code_db.path = str(code_db_file)
    mock_code_db.stats.return_value = {"files": 50, "segments": 150, "vectors": 120}

    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db, code_db=mock_code_db)
    dialog.storage_tab.update_database_stats()

    label_text = dialog.storage_tab.stats_label.text()
    assert "Indexed Files: 150" in label_text
    assert "Segments: 450" in label_text
    assert "Vectors: 370" in label_text
    assert "3.0 KB" in label_text
    dialog.shutdown()


def test_settings_dialog_stats_no_databases(tmp_path: Path, mock_ollama: MagicMock, qapp: QApplication) -> None:
    """Verify stats label when both db and code_db are None."""
    del qapp
    cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")
    dialog = SettingsDialog(cfg_mgr, mock_ollama, db=None, code_db=None)
    dialog.storage_tab.update_database_stats()
    assert dialog.storage_tab.stats_label.text() == "Database metrics unavailable"
    dialog.shutdown()


def test_settings_dialog_async_vacuum_disables_button_and_runs_both_databases(
    tmp_path: Path, mock_ollama: MagicMock, qapp: QApplication
) -> None:
    """Verify _vacuum_database executes off the UI thread, disables button while running, and refreshes stats."""
    cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")
    vacuum_started = threading.Event()
    unblock_vacuum = threading.Event()

    mock_db = create_autospec(Database, instance=True)
    mock_db.path = str(tmp_path / "index.db")
    mock_db.stats.return_value = {"files": 10, "segments": 20, "vectors": 20}

    def slow_vacuum() -> None:
        vacuum_started.set()
        unblock_vacuum.wait(timeout=2.0)

    mock_db.vacuum.side_effect = slow_vacuum

    mock_code_db = create_autospec(Database, instance=True)
    mock_code_db.path = str(tmp_path / "code.db")
    mock_code_db.stats.return_value = {"files": 5, "segments": 10, "vectors": 10}

    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db, code_db=mock_code_db)
    dialog.storage_tab.vacuum_btn.click()

    assert vacuum_started.wait(timeout=1.0)
    assert not dialog.storage_tab.vacuum_btn.isEnabled()

    unblock_vacuum.set()
    assert _process_until(qapp, dialog.storage_tab.vacuum_btn.isEnabled)
    mock_db.vacuum.assert_called_once()
    mock_code_db.vacuum.assert_called_once()
    dialog.shutdown()


def test_settings_dialog_force_reindex_and_reindex_signals(
    tmp_path: Path, mock_ollama: MagicMock, mock_db: MagicMock, qapp: QApplication
) -> None:
    """Verify clicking Re-index All Files emits both force_reindex_requested and reindex_requested."""
    del qapp
    cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")
    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db)

    tab_reindex_spy = MagicMock()
    dialog_reindex_spy = MagicMock()
    tab_force_spy = MagicMock()
    dialog_force_spy = MagicMock()

    dialog.storage_tab.reindex_requested.connect(tab_reindex_spy)
    dialog.reindex_requested.connect(dialog_reindex_spy)
    dialog.storage_tab.force_reindex_requested.connect(tab_force_spy)
    dialog.force_reindex_requested.connect(dialog_force_spy)

    dialog.storage_tab.reindex_btn.click()

    tab_reindex_spy.assert_called_once()
    dialog_reindex_spy.assert_called_once()
    tab_force_spy.assert_called_once()
    dialog_force_spy.assert_called_once()
    dialog.shutdown()


def test_settings_dialog_stats_code_db_only(tmp_path: Path, mock_ollama: MagicMock, qapp: QApplication) -> None:
    """Verify update_database_stats works when db is None but code_db is present."""
    del qapp
    cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")

    code_db_file = tmp_path / "code.db"
    code_db_file.write_bytes(b"x" * 1024)
    mock_code_db = create_autospec(Database, instance=True)
    mock_code_db.path = str(code_db_file)
    mock_code_db.stats.return_value = {"files": 42, "segments": 84, "vectors": 70}

    dialog = SettingsDialog(cfg_mgr, mock_ollama, db=None, code_db=mock_code_db)
    dialog.storage_tab.update_database_stats()

    label_text = dialog.storage_tab.stats_label.text()
    assert "Indexed Files: 42" in label_text
    assert "Segments: 84" in label_text
    assert "Vectors: 70" in label_text
    assert "1.0 KB" in label_text
    dialog.shutdown()


def test_settings_dialog_vacuum_real_databases(tmp_path: Path, mock_ollama: MagicMock, qapp: QApplication) -> None:
    """Verify async _vacuum_database executes VACUUM on real SQLite database instances."""
    cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")

    real_db = Database(tmp_path / "real_index.db")
    real_code_db = Database(tmp_path / "real_code.db")

    dialog = SettingsDialog(cfg_mgr, mock_ollama, real_db, code_db=real_code_db)
    dialog.storage_tab.vacuum_btn.click()
    assert _process_until(
        qapp,
        lambda: dialog.storage_tab.vacuum_btn.isEnabled() and dialog.storage_tab._vacuum_worker is None,
    )

    assert "Indexed Files: 0" in dialog.storage_tab.stats_label.text()
    assert "Segments: 0" in dialog.storage_tab.stats_label.text()
    assert "8.0 KB" in dialog.storage_tab.stats_label.text()
    dialog.shutdown()
    real_db.close()
    real_code_db.close()


def test_settings_dialog_num_ctx_validation_uses_temporary_client(
    tmp_path: Path,
    mock_ollama: MagicMock,
    mock_db: MagicMock,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify num_ctx validation probes a temporary closed OllamaClient without mutating shared client."""
    del qapp
    probes: list[_FakeProbeClient] = []

    def make_probe(*, base_url: str) -> _FakeProbeClient:
        probe = _FakeProbeClient(base_url, max_ctx=8192)
        probes.append(probe)
        return probe

    monkeypatch.setattr("semsearch.gui.settings.OllamaClient", make_probe)

    cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")
    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db)
    dialog.storage_tab.url_edit.setText("http://probe-host:11434")
    assert dialog.storage_tab.num_ctx_edit.text() == "0"

    # 1. Invalid non-numeric input is rejected
    dialog.storage_tab.num_ctx_edit.setText("abc")
    dialog.save_settings()
    assert cfg_mgr.config.num_ctx == 0
    assert "Invalid context window" in dialog.storage_tab.status_label.text()

    # 2. Value exceeding model maximum context_length is rejected without mutating shared client
    dialog.storage_tab.num_ctx_edit.setText("16384")
    dialog.save_settings()
    assert cfg_mgr.config.num_ctx == 0
    assert "exceeds model maximum (8192)" in dialog.storage_tab.status_label.text()
    assert mock_ollama.base_url == "http://localhost:11434"
    assert probes[-1].closed is True

    # 3. Valid value within model maximum is accepted and saved
    dialog.storage_tab.num_ctx_edit.setText("4096")
    dialog.save_settings()
    assert cfg_mgr.config.num_ctx == 4096
    assert probes[-1].closed is True


def test_autostart_respects_xdg_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify autostart resolves under $XDG_CONFIG_HOME when module constants are not monkeypatched."""
    xdg_config = tmp_path / "xdg_cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/semsearch")

    expected = xdg_config / "autostart" / "semsearch.desktop"
    assert not is_autostart_enabled()
    set_autostart_enabled(enabled=True)
    assert expected.is_file()
    assert is_autostart_enabled()
    set_autostart_enabled(enabled=False)
    assert not expected.exists()
    assert not is_autostart_enabled()


def test_settings_dialog_folders_and_code_new_controls(
    tmp_path: Path, mock_ollama: MagicMock, mock_db: MagicMock, qapp: QApplication
) -> None:
    """Verify FoldersTab indexing behavior controls, CodeTab custom editor, and manual editor preservation."""
    del qapp
    cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")
    cfg_mgr.config.follow_symlinks = True
    cfg_mgr.config.skip_code_projects = False
    cfg_mgr.config.skip_datasets = False
    cfg_mgr.config.max_media_duration_min = 0
    cfg_mgr.config.code_editor = "my-custom-editor"
    cfg_mgr.config.code_watch_delay_sec = 15

    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db)

    assert dialog.folders_tab.skipped_ext_label.text() == "Skipped Extensions / Kinds:"
    assert "video" in dialog.folders_tab.skipped_ext_edit.placeholderText()
    assert dialog.folders_tab.follow_symlinks_check.isChecked() is True
    assert dialog.folders_tab.skip_code_projects_check.isChecked() is False
    assert dialog.folders_tab.skip_datasets_check.isChecked() is False
    assert dialog.folders_tab.max_media_duration_spin.value() == 0

    # Unknown manual code_editor is preserved under Custom Command instead of resetting to auto
    assert dialog.code_tab.editor_combo.currentData() == "custom"
    assert dialog.code_tab.custom_editor_edit.text() == "my-custom-editor"
    assert not dialog.code_tab.custom_editor_edit.isHidden()
    assert dialog.code_tab.code_watch_delay_spin.value() == 15

    # Switching away from custom hides custom command field
    kate_idx = dialog.code_tab.editor_combo.findData("kate")
    assert kate_idx >= 0
    dialog.code_tab.editor_combo.setCurrentIndex(kate_idx)
    assert dialog.code_tab.custom_editor_edit.isHidden()

    # Switching back to custom and saving persists all fields
    custom_idx = dialog.code_tab.editor_combo.findData("custom")
    dialog.code_tab.editor_combo.setCurrentIndex(custom_idx)
    assert not dialog.code_tab.custom_editor_edit.isHidden()
    dialog.code_tab.custom_editor_edit.setText("nvim-qt +{line} {file}")
    dialog.code_tab.code_watch_delay_spin.setValue(30)
    dialog.folders_tab.follow_symlinks_check.setChecked(False)
    dialog.folders_tab.skip_code_projects_check.setChecked(True)
    dialog.folders_tab.skip_datasets_check.setChecked(True)
    dialog.folders_tab.max_media_duration_spin.setValue(90)

    dialog.save_settings()
    saved = cfg_mgr.config
    assert saved.follow_symlinks is False
    assert saved.skip_code_projects is True
    assert saved.skip_datasets is True
    assert saved.max_media_duration_min == 90
    assert saved.code_editor == "custom"
    assert saved.custom_editor_cmd == "nvim-qt +{line} {file}"
    assert saved.code_watch_delay_sec == 30


def test_settings_dialog_storage_tab_new_controls_and_api_key_probe(
    tmp_path: Path,
    mock_ollama: MagicMock,
    mock_db: MagicMock,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify keep_alive, timeout, API key propagation to probes, and data_dir browse/save."""
    del qapp
    seen_calls: list[dict[str, str]] = []

    def make_probe(*, base_url: str, api_key: str = "") -> _FakeProbeClient:
        seen_calls.append({"base_url": base_url, "api_key": api_key})
        return _FakeProbeClient(base_url, models=["embeddinggemma-2"], max_ctx=8192)

    monkeypatch.setattr("semsearch.gui.widgets.OllamaClient", make_probe)
    monkeypatch.setattr("semsearch.gui.settings.OllamaClient", make_probe)

    custom_dir = tmp_path / "custom_index"
    monkeypatch.setattr(
        "semsearch.gui.settings.QFileDialog.getExistingDirectory",
        staticmethod(lambda *_args: str(custom_dir)),
    )

    cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")
    cfg_mgr.config.ollama_keep_alive = "keep_loaded"
    cfg_mgr.config.ollama_timeout = 240
    cfg_mgr.config.ollama_api_key = "initial-secret"
    cfg_mgr.config.data_dir = str(tmp_path / "old_index")

    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db)

    assert dialog.storage_tab.keep_alive_combo.currentData() == "keep_loaded"
    assert dialog.storage_tab.timeout_spin.value() == 240
    assert dialog.storage_tab.api_key_edit.echoMode() == QLineEdit.EchoMode.Password
    assert dialog.storage_tab.api_key_edit.text() == "initial-secret"
    assert dialog.storage_tab.ollama_picker.api_key() == "initial-secret"
    assert dialog.storage_tab.data_dir_edit.text() == str(tmp_path / "old_index")

    # Editing api_key updates picker and passes api_key on probe + num_ctx validation
    dialog.storage_tab.api_key_edit.setText("live-key-123")
    dialog.storage_tab.test_connection()
    assert seen_calls[-1]["api_key"] == "live-key-123"

    dialog.storage_tab.num_ctx_edit.setText("2048")
    assert dialog.storage_tab.validate_num_ctx() == 2048
    assert seen_calls[-1]["api_key"] == "live-key-123"

    # Browsing updates data_dir_edit and saving persists all StorageModelTab fields
    dialog.storage_tab.data_dir_browse_btn.click()
    assert dialog.storage_tab.data_dir_edit.text() == str(custom_dir)
    server_idx = dialog.storage_tab.keep_alive_combo.findData("server")
    dialog.storage_tab.keep_alive_combo.setCurrentIndex(server_idx)
    dialog.storage_tab.timeout_spin.setValue(300)

    dialog.save_settings()
    saved = cfg_mgr.config
    assert saved.ollama_keep_alive == "server"
    assert saved.ollama_timeout == 300
    assert saved.ollama_api_key == "live-key-123"
    assert saved.data_dir == str(custom_dir)


def test_settings_dialog_general_tab_new_controls_and_theme_refresh(
    tmp_path: Path, mock_ollama: MagicMock, mock_db: MagicMock, qapp: QApplication
) -> None:
    """Verify theme, terminal/custom_terminal, panel toggles, and live refresh_theme()."""
    del qapp
    cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")
    cfg_mgr.config.theme = "light"
    cfg_mgr.config.terminal = "custom"
    cfg_mgr.config.custom_terminal_cmd = "my-term --dir {dir}"
    cfg_mgr.config.close_on_focus_loss = False
    cfg_mgr.config.remember_last_query = True

    dialog = SettingsDialog(cfg_mgr, mock_ollama, mock_db)

    assert dialog.general_tab.theme_combo.currentData() == "light"
    assert dialog.general_tab.terminal_combo.currentData() == "custom"
    assert not dialog.general_tab.custom_terminal_edit.isHidden()
    assert dialog.general_tab.custom_terminal_edit.text() == "my-term --dir {dir}"
    assert dialog.general_tab.close_on_focus_loss_check.isChecked() is False
    assert dialog.general_tab.remember_last_query_check.isChecked() is True

    # Switching terminal to built-in hides custom input
    kitty_idx = dialog.general_tab.terminal_combo.findData("kitty")
    dialog.general_tab.terminal_combo.setCurrentIndex(kitty_idx)
    assert dialog.general_tab.custom_terminal_edit.isHidden()

    # Switching theme and refreshing updates QSS palette
    dialog.refresh_theme("dark")
    assert "#16181D" in dialog.styleSheet()
    dialog.refresh_theme("light")
    assert "#F3F4F6" in dialog.styleSheet()

    # Save updated GeneralTab controls
    sys_idx = dialog.general_tab.theme_combo.findData("system")
    dialog.general_tab.theme_combo.setCurrentIndex(sys_idx)
    dialog.general_tab.close_on_focus_loss_check.setChecked(True)
    dialog.general_tab.remember_last_query_check.setChecked(False)
    dialog.save_settings()

    saved = cfg_mgr.config
    assert saved.theme == "system"
    assert saved.terminal == "kitty"
    assert saved.close_on_focus_loss is True
    assert saved.remember_last_query is False
