"""Unit and widget tests for OnboardingWizard."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from semsearch.config import ConfigManager
from semsearch.gui.onboarding import OnboardingWizard


@pytest.fixture
def mock_ollama() -> MagicMock:
    client = MagicMock()
    client.list_multimodal_models.return_value = ["embeddinggemma-2"]
    return client


def test_onboarding_wizard_pages_sequence(tmp_path: Path, mock_ollama: MagicMock, qapp: QApplication) -> None:
    """Verify 5-step page sequence in wizard."""
    del qapp
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)
    assert not cfg_mgr.config.onboarded

    wizard = OnboardingWizard(cfg_mgr, mock_ollama)
    assert wizard.pageIds() == [0, 1, 2, 3, 4]
    wizard.restart()
    assert wizard.currentId() == 0

    # Page 1 Welcome
    assert "Welcome" in wizard.welcome_page.title()

    # Page 2 Folders
    folders = wizard.folders_page.get_selected_folders()
    assert len(folders) >= 1

    # Page 3 Model
    wizard.model_page.test_connection()
    assert wizard.model_page.model_combo.count() == 1
    assert wizard.model_page.model_combo.currentText() == "embeddinggemma-2"


def test_onboarding_wizard_completion_persists_config(
    tmp_path: Path, mock_ollama: MagicMock, qapp: QApplication
) -> None:
    """Verify finishing wizard marks onboarded=True and emits indexing signal."""
    del qapp
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)

    wizard = OnboardingWizard(cfg_mgr, mock_ollama)
    spy_indexing = MagicMock()
    wizard.indexing_started.connect(spy_indexing)

    wizard.accept()

    spy_indexing.assert_called_once()
    assert cfg_mgr.config.onboarded is True

    # Re-reading config confirms persistence
    reloaded_cfg = ConfigManager(config_path=cfg_file).config
    assert reloaded_cfg.onboarded is True


def test_onboarding_wizard_connection_error_handling(tmp_path: Path, qapp: QApplication) -> None:
    """Verify Ollama connection error is handled gracefully on onboarding model page."""
    del qapp
    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)
    mock_ollama = MagicMock()
    mock_ollama.list_multimodal_models.side_effect = ConnectionError("Connection refused")

    wizard = OnboardingWizard(cfg_mgr, mock_ollama)
    wizard.model_page.test_connection()
    assert "Connection failed" in wizard.model_page.status_label.text()
