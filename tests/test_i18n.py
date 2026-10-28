"""Comprehensive unit and widget tests for i18n support across individual language modules."""

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from semsearch.config import AppConfig, ConfigManager
from semsearch.core.types import EngineActivity, EngineStatus, FileKind, SearchHit
from semsearch.gui.onboarding import OnboardingWizard
from semsearch.gui.panel import SearchPanel
from semsearch.gui.preview import PreviewPane
from semsearch.gui.settings import SettingsDialog
from semsearch.gui.tray import SystemTrayManager
from semsearch.i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    TRANSLATIONS,
    detect_system_language,
    get_language,
    is_rtl,
    normalize_language_code,
    resolve_language,
    set_language,
    tr,
)
from semsearch.main import build_parser
from semsearch.services.searcher import ResultSet

PLACEHOLDER_RE = re.compile(r"\{[a-zA-Z0-9_]+\}")

EXPECTED_TRANSLATION_LANGUAGES = {
    "es",
    "fr",
    "de",
    "it",
    "pt",
    "nl",
    "pl",
    "ar",
    "tr",
    "fa",
    "he",
    "id",
    "vi",
    "th",
    "ms",
    "tl",
    "ru",
    "uk",
    "be",
    "bg",
    "sr",
    "kk",
}


def test_default_language_is_english_without_en_file() -> None:
    """Verify English ('en') is the default language using inline source strings directly."""
    assert DEFAULT_LANGUAGE == "en"
    assert get_language() == "en"
    assert "en" not in TRANSLATIONS
    assert not (Path(__file__).parent.parent / "src" / "semsearch" / "i18n" / "en.py").exists()
    assert tr("Welcome to SemSearch") == "Welcome to SemSearch"
    assert tr("Page {current} of {total}", current=2, total=5) == "Page 2 of 5"


def test_supported_languages_and_individual_files() -> None:
    """Verify each translated language has its own dedicated module file."""
    assert set(TRANSLATIONS.keys()) == EXPECTED_TRANSLATION_LANGUAGES
    assert set(SUPPORTED_LANGUAGES.keys()) == EXPECTED_TRANSLATION_LANGUAGES | {"en"}

    i18n_dir = Path(__file__).parent.parent / "src" / "semsearch" / "i18n"
    for lang_code in EXPECTED_TRANSLATION_LANGUAGES:
        assert (i18n_dir / f"{lang_code}.py").is_file()


@pytest.mark.parametrize("lang_code", sorted(EXPECTED_TRANSLATION_LANGUAGES))
def test_translation_catalog_completeness_and_placeholders(lang_code: str) -> None:
    """Verify every language catalog has identical keys and preserves format placeholders."""
    reference_keys = set(TRANSLATIONS["es"].keys())
    catalog = TRANSLATIONS[lang_code]
    missing_keys = reference_keys - set(catalog.keys())
    extra_keys = set(catalog.keys()) - reference_keys
    assert not missing_keys, f"Language '{lang_code}' is missing keys: {missing_keys}"
    assert not extra_keys, f"Language '{lang_code}' has unexpected keys: {extra_keys}"

    for source_key, translated_val in catalog.items():
        assert isinstance(translated_val, str) and translated_val.strip(), (
            f"Empty translation for key '{source_key}' in '{lang_code}'"
        )
        en_placeholders = set(PLACEHOLDER_RE.findall(source_key))
        tr_placeholders = set(PLACEHOLDER_RE.findall(translated_val))
        assert en_placeholders == tr_placeholders, (
            f"Placeholder mismatch in '{lang_code}' for key '{source_key}': "
            f"expected {en_placeholders}, got {tr_placeholders}"
        )


def test_unsupported_language_fallback_to_english(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify unsupported locales and unknown codes fall back to English ('en')."""
    for unsupported in ("sw_KE.UTF-8", "zu", "eo", "xx_YY", "C", "POSIX", ""):
        assert normalize_language_code(unsupported) == "en"
        assert set_language(unsupported) == "en"
        assert get_language() == "en"
        assert tr("Quick Search") == "Quick Search"

    monkeypatch.setenv("LC_ALL", "sw_KE.UTF-8")
    monkeypatch.setenv("LANG", "sw_KE.UTF-8")
    assert detect_system_language() == "en"
    assert resolve_language("auto") == "en"


def test_system_locale_detection_and_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify locale detection from environment variables and ISO aliases."""
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.delenv("LANGUAGE", raising=False)

    monkeypatch.setenv("LANG", "es_ES.UTF-8")
    assert detect_system_language() == "es"
    assert set_language("auto") == "es"
    assert tr("Quick Search") != "Quick Search"

    monkeypatch.setenv("LC_ALL", "ru_RU.UTF-8")
    assert detect_system_language() == "ru"

    assert normalize_language_code("fil_PH.UTF-8") == "tl"
    assert normalize_language_code("in_ID.UTF-8") == "id"
    assert normalize_language_code("iw_IL.UTF-8") == "he"


def test_rtl_language_detection() -> None:
    """Verify Right-to-Left layout flag for Arabic, Persian, and Hebrew."""
    for rtl_code in ("ar", "fa", "he"):
        set_language(rtl_code)
        assert is_rtl() is True
        assert is_rtl(rtl_code) is True

    for ltr_code in ("en", "es", "fr", "ru", "id", "vi", "th", "tr"):
        set_language(ltr_code)
        assert is_rtl() is False
        assert is_rtl(ltr_code) is False


def test_gui_live_retranslation_and_components(tmp_path: Path, qapp: QApplication) -> None:
    """Verify GUI widgets translate and dynamically retranslate when switching languages."""
    mock_search = MagicMock()
    mock_search.search.return_value = ResultSet()
    mock_ollama = MagicMock()
    mock_ollama.list_multimodal_models.return_value = ["embeddinggemma-2"]

    cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")

    set_language("es")
    tray = SystemTrayManager()
    panel = SearchPanel(mock_search, AppConfig())
    preview = PreviewPane()
    settings = SettingsDialog(cfg_mgr, mock_ollama)
    wizard = OnboardingWizard(cfg_mgr, mock_ollama)

    assert tray.quit_action.text() == TRANSLATIONS["es"]["Quit"]
    assert panel.footer_hint.text() == TRANSLATIONS["es"]["↵ Open    ⇧↵ Show in Files    ⌃C Copy    Esc Dismiss"]
    assert settings.windowTitle() == TRANSLATIONS["es"]["SemSearch Settings"]
    assert wizard.welcome_page.title() == TRANSLATIONS["es"]["Welcome to SemSearch"]

    # Switch to Russian dynamically and retranslate
    set_language("ru")
    tray.retranslate_ui()
    panel.retranslate_ui()
    preview.retranslate_ui()
    settings.retranslate_ui()

    assert tray.quit_action.text() == TRANSLATIONS["ru"]["Quit"]
    assert panel.footer_hint.text() == TRANSLATIONS["ru"]["↵ Open    ⇧↵ Show in Files    ⌃C Copy    Esc Dismiss"]
    assert preview.empty_view.text_label.text() == TRANSLATIONS["ru"]["Select an item to preview"]
    assert settings.windowTitle() == TRANSLATIONS["ru"]["SemSearch Settings"]

    # Verify status formatting with placeholders in Arabic
    set_language("ar")
    tray.update_status(EngineStatus(activity=EngineActivity.INDEXING, indexed=25, total=50))
    expected_ar = tr("Indexing: {percent}% ({indexed}/{total})", percent=50, indexed=25, total=50)
    assert tray.status_action.text() == expected_ar

    # Verify SearchHit.reason and CLI parser localization
    hit = SearchHit(path="/tmp/a.pdf", kind=FileKind.PDF, score=1.0, z=2.5)
    assert hit.reason == TRANSLATIONS["ar"]["Matches meaning"]
    parser = build_parser()
    assert parser.description == TRANSLATIONS["ar"]["SemSearch - Linux Desktop Semantic Search Engine"]
