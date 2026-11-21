"""Architectural invariants, zero OCR audit, and GUI parity tests."""

import ast
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from semsearch.config import AppConfig, ConfigManager
from semsearch.core.database import Database
from semsearch.core.ollama import OllamaClient
from semsearch.core.types import SegmentKind
from semsearch.gui.onboarding import OnboardingWizard
from semsearch.gui.panel import SearchPanel
from semsearch.gui.settings import SettingsDialog
from semsearch.gui.tray import SystemTrayManager

BANNED_OCR_LIBRARIES = {
    "pytesseract",
    "tesseract",
    "easyocr",
    "paddleocr",
    "ocrmypdf",
    "tesserocr",
}


def test_zero_ocr_architectural_invariant() -> None:
    src_dir = Path(__file__).resolve().parent.parent / "src" / "semsearch"
    assert src_dir.is_dir()
    assert not hasattr(SegmentKind, "OCR")

    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name_root = alias.name.split(".")[0].lower()
                    assert name_root not in BANNED_OCR_LIBRARIES, (
                        f"Banned OCR library {alias.name} imported in {py_file}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                mod_root = node.module.split(".")[0].lower()
                assert mod_root not in BANNED_OCR_LIBRARIES, f"Banned OCR module {node.module} imported in {py_file}"

        lower_content = content.lower()
        for banned in BANNED_OCR_LIBRARIES:
            assert banned not in lower_content, f"Occurrence of banned OCR string {banned} detected in {py_file}"


def test_multimodal_model_default_and_filtering_invariant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = AppConfig()
    assert cfg.model == "embeddinggemma-2"

    cfg_mgr = ConfigManager(tmp_path / "config.json")
    assert cfg_mgr.config.model == "embeddinggemma-2"

    mock_tags: list[dict[str, Any]] = [
        {"name": "embeddinggemma-2:latest", "details": {"families": ["gemma-embedding2"]}},
        {"name": "nomic-embed-text:latest", "details": {"families": ["nomic-bert"]}},
        {"name": "bge-m3:latest", "details": {"families": ["bert"]}},
        {"name": "all-minilm:latest", "details": {"families": ["bert"]}},
        {"name": "qwen3.8:latest", "capabilities": ["completion", "vision", "tools"]},
        {"name": "llava:latest", "details": {"families": ["clip", "llama"]}},
    ]
    show_payloads: dict[str, dict[str, Any]] = {
        "embeddinggemma-2:latest": {
            "capabilities": ["embedding", "vision"],
            "details": {"families": ["gemma-embedding2"]},
        },
        "nomic-embed-text:latest": {
            "capabilities": ["embedding"],
            "details": {"families": ["nomic-bert"]},
        },
        "bge-m3:latest": {
            "capabilities": ["embedding"],
            "details": {"families": ["bert"]},
        },
        "all-minilm:latest": {
            "capabilities": ["embedding"],
            "details": {"families": ["bert"]},
        },
        "qwen3.8:latest": {
            "capabilities": ["completion", "vision", "tools"],
            "details": {"families": ["qwen3"]},
        },
        "llava:latest": {
            "capabilities": ["completion", "vision"],
            "details": {"families": ["clip", "llama"]},
        },
    }

    with OllamaClient() as ollama:
        monkeypatch.setattr(ollama, "get_tags", lambda: mock_tags)
        monkeypatch.setattr(ollama, "show_model", lambda name: show_payloads[name])

        filtered = ollama.list_multimodal_models()
        assert filtered == ["embeddinggemma-2:latest"]


def test_gui_component_parity(qapp: QApplication, tmp_path: Path) -> None:
    del qapp
    mock_search = MagicMock()
    mock_search.search.return_value = MagicMock(ranked=[], weaker=[])
    cfg = AppConfig()

    panel = SearchPanel(search_service=mock_search, app_config=cfg)
    assert panel is not None
    assert panel.search_input is not None
    assert panel.result_list is not None
    assert panel.preview_pane is not None

    tray = SystemTrayManager()
    assert tray is not None
    assert tray.tray_icon is not None

    mock_ollama = MagicMock()
    mock_ollama.list_multimodal_models.return_value = ["embeddinggemma-2"]
    with Database(tmp_path / "settings_test.db") as db:
        cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")
        settings = SettingsDialog(cfg_mgr, db=db, ollama_client=mock_ollama)
        assert settings is not None
        assert settings.tabs.count() == 4
        tab_names = [settings.tabs.tabText(i).replace("&&", "&") for i in range(settings.tabs.count())]
        assert "Folders" in tab_names
        assert "Code" in tab_names
        assert "Storage & Model" in tab_names
        assert "General" in tab_names

        wizard = OnboardingWizard(cfg_mgr, mock_ollama)
        assert wizard is not None
        assert wizard.pageIds() == [0, 1, 2, 3, 4]
        settings.shutdown()
    panel.shutdown()
