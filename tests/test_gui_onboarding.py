"""Unit and widget tests for OnboardingWizard, XDG QStandardPaths discovery, and OllamaModelPicker."""

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, create_autospec

import pytest
from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QApplication

from semsearch.config import ConfigManager
from semsearch.core.ollama import OllamaClient
from semsearch.gui.onboarding import OnboardingWizard, discover_standard_folders


class _FakeOnboardingProbe:
    """Short-lived fake OllamaClient tracking lifecycle for onboarding model discovery tests."""

    def __init__(self, base_url: str, *, models: list[str] | None = None, error: Exception | None = None) -> None:
        self.base_url = base_url
        self._models = models if models is not None else ["embeddinggemma-2"]
        self._error = error
        self.closed = False

    def __enter__(self) -> "_FakeOnboardingProbe":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self.closed = True

    def list_multimodal_models(self) -> list[str]:
        if self._error is not None:
            raise self._error
        return list(self._models)


@pytest.fixture
def mock_ollama() -> MagicMock:
    client = cast(MagicMock, create_autospec(OllamaClient, instance=True))
    client.base_url = "http://localhost:11434"
    return client


def test_discover_standard_folders_uses_xdg_qstandardpaths_and_deduplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify QStandardPaths localized folders are discovered, missing ones filtered, and duplicates removed."""
    docs = tmp_path / "Documentos"
    pics = tmp_path / "Imágenes"
    desk = tmp_path / "Escritorio"
    docs.mkdir()
    pics.mkdir()
    desk.mkdir()
    missing_downloads = tmp_path / "Descargas"

    mapping = {
        QStandardPaths.StandardLocation.DocumentsLocation: str(docs),
        QStandardPaths.StandardLocation.PicturesLocation: str(pics),
        QStandardPaths.StandardLocation.DesktopLocation: str(docs),  # duplicate of Documents
        QStandardPaths.StandardLocation.DownloadLocation: str(missing_downloads),  # non-existent
    }
    monkeypatch.setattr(QStandardPaths, "writableLocation", lambda loc: mapping.get(loc, ""))

    found = discover_standard_folders()
    assert [(name, path, checked) for name, path, checked in found] == [
        ("Documents", docs.resolve(), True),
        ("Pictures", pics.resolve(), True),
    ]

    # Empty QStandardPaths return falls back to Path.home() / name if that directory exists
    fake_home = tmp_path / "fake_home"
    (fake_home / "Documents").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: fake_home))
    monkeypatch.setattr(QStandardPaths, "writableLocation", lambda _loc: "")

    fallback_found = discover_standard_folders()
    assert fallback_found == [("Documents", (fake_home / "Documents").resolve(), True)]


def test_onboarding_wizard_pages_sequence_and_non_mutating_probe(
    tmp_path: Path, mock_ollama: MagicMock, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify 5-step sequence, XDG folder checkboxes, and non-mutating Ollama connection probe."""
    del qapp
    docs = tmp_path / "Documentos"
    pics = tmp_path / "Imágenes"
    docs.mkdir()
    pics.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    mapping = {
        QStandardPaths.StandardLocation.DocumentsLocation: str(docs),
        QStandardPaths.StandardLocation.PicturesLocation: str(pics),
        QStandardPaths.StandardLocation.DesktopLocation: "",
        QStandardPaths.StandardLocation.DownloadLocation: "",
    }
    monkeypatch.setattr(QStandardPaths, "writableLocation", lambda loc: mapping.get(loc, ""))

    probes: list[_FakeOnboardingProbe] = []

    def make_probe(*, base_url: str) -> _FakeOnboardingProbe:
        probe = _FakeOnboardingProbe(base_url, models=["embeddinggemma-2", "vision-embed"])
        probes.append(probe)
        return probe

    monkeypatch.setattr("semsearch.gui.widgets.OllamaClient", make_probe)

    cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")
    assert not cfg_mgr.config.onboarded

    wizard = OnboardingWizard(cfg_mgr, mock_ollama)
    assert wizard.pageIds() == [0, 1, 2, 3, 4]
    wizard.restart()
    assert wizard.currentId() == 0

    assert "Welcome" in wizard.welcome_page.title()
    assert wizard.code_page.title() == "Developer & Code Search"

    folders = wizard.folders_page.get_selected_folders()
    assert folders == [str(docs.resolve()), str(pics.resolve())]

    wizard.model_page.url_edit.setText("http://preview-host:11434")
    wizard.model_page.test_connection()
    assert wizard.model_page.model_combo.count() == 2
    assert wizard.model_page.model_combo.currentText() == "embeddinggemma-2"
    assert len(probes) == 1
    assert probes[0].closed is True
    # Shared ollama client is NOT mutated before accept()
    assert mock_ollama.base_url == "http://localhost:11434"

    wizard.reject()
    assert mock_ollama.base_url == "http://localhost:11434"
    assert not cfg_mgr.config.onboarded


def test_onboarding_wizard_completion_persists_config(
    tmp_path: Path, mock_ollama: MagicMock, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify finishing wizard persists XDG folders, model URL, onboarded=True, and emits indexing signal."""
    del qapp
    docs = tmp_path / "Documentos"
    docs.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(
        QStandardPaths,
        "writableLocation",
        lambda loc: str(docs) if loc == QStandardPaths.StandardLocation.DocumentsLocation else "",
    )

    cfg_file = tmp_path / "config.json"
    cfg_mgr = ConfigManager(config_path=cfg_file)

    wizard = OnboardingWizard(cfg_mgr, mock_ollama)
    wizard.model_page.url_edit.setText("http://127.0.0.1:11434")
    wizard.code_page.code_enable_check.setChecked(True)
    wizard.code_page.repos_editor.add_folder(str(tmp_path / "repo"))

    spy_indexing = MagicMock()
    wizard.indexing_started.connect(spy_indexing)

    wizard.accept()

    spy_indexing.assert_called_once()
    assert cfg_mgr.config.onboarded is True
    assert cfg_mgr.config.folders == [str(docs.resolve())]
    assert cfg_mgr.config.ollama_url == "http://127.0.0.1:11434"
    assert mock_ollama.base_url == "http://127.0.0.1:11434"
    assert cfg_mgr.config.code_enabled is True
    assert cfg_mgr.config.code_folders == [str(tmp_path / "repo")]

    reloaded_cfg = ConfigManager(config_path=cfg_file).config
    assert reloaded_cfg.onboarded is True
    assert reloaded_cfg.folders == [str(docs.resolve())]


def test_onboarding_wizard_connection_error_handling(
    tmp_path: Path, mock_ollama: MagicMock, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify Ollama connection error is formatted cleanly on onboarding model page."""
    del qapp
    probes: list[_FakeOnboardingProbe] = []

    def failing_probe(*, base_url: str) -> _FakeOnboardingProbe:
        probe = _FakeOnboardingProbe(base_url, error=ConnectionError("Connection refused"))
        probes.append(probe)
        return probe

    monkeypatch.setattr("semsearch.gui.widgets.OllamaClient", failing_probe)

    cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")
    wizard = OnboardingWizard(cfg_mgr, mock_ollama)
    wizard.model_page.test_connection()
    assert "Connection failed" in wizard.model_page.status_label.text()
    assert len(probes) == 1
    assert probes[0].closed is True
    wizard.reject()
