"""Comprehensive unit and widget tests for lazy JSON i18n catalogs and locale detection."""

import ast
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

import semsearch.i18n as i18n_mod
from semsearch.config import AppConfig, ConfigManager
from semsearch.core.types import EngineActivity, EngineStatus, FileKind, MatchReason, SearchHit
from semsearch.gui.onboarding import OnboardingWizard
from semsearch.gui.panel import SearchPanel
from semsearch.gui.preview import PreviewPane
from semsearch.gui.settings import SettingsDialog
from semsearch.gui.tray import SystemTrayManager
from semsearch.i18n import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    detect_system_language,
    get_language,
    is_rtl,
    load_catalog,
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
    i18n_dir = Path(__file__).parent.parent / "src" / "semsearch" / "i18n"
    assert DEFAULT_LANGUAGE == "en"
    assert get_language() == "en"
    assert load_catalog("en") == {}
    assert not (i18n_dir / "en.py").exists()
    assert not (i18n_dir / "locales" / "en.json").exists()
    assert tr("Welcome to SemSearch") == "Welcome to SemSearch"
    assert tr("Page {current} of {total}", current=2, total=5) == "Page 2 of 5"


def test_lazy_catalog_loading() -> None:
    """Verify importing semsearch.i18n with English loads no catalogs and loads per-language on demand."""
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import semsearch.i18n as m; "
                "assert m.get_language() == 'en'; "
                "assert m._loaded_catalogs == {}; "
                "assert m.tr('Quick Search') == 'Quick Search'; "
                "assert m._loaded_catalogs == {}"
            ),
        ],
        check=True,
    )

    i18n_mod._loaded_catalogs.clear()
    set_language("en")
    assert tr("Quick Search") == "Quick Search"
    assert i18n_mod._loaded_catalogs == {}

    set_language("es")
    assert tr("Quick Search") != "Quick Search"
    assert set(i18n_mod._loaded_catalogs.keys()) == {"es"}


def test_supported_languages_json_files_and_no_legacy_py() -> None:
    """Verify 22 JSON locale files exist and all 22 legacy per-language .py modules are removed."""
    assert set(SUPPORTED_LANGUAGES.keys()) == EXPECTED_TRANSLATION_LANGUAGES | {"en"}

    i18n_dir = Path(__file__).parent.parent / "src" / "semsearch" / "i18n"
    locales_dir = i18n_dir / "locales"

    json_langs = {p.stem for p in locales_dir.glob("*.json")}
    assert json_langs == EXPECTED_TRANSLATION_LANGUAGES

    py_files = {p.name for p in i18n_dir.glob("*.py")}
    assert py_files == {"__init__.py"}
    for lang_code in EXPECTED_TRANSLATION_LANGUAGES:
        assert not (i18n_dir / f"{lang_code}.py").exists()


def test_all_source_tr_keys_present_in_catalogs() -> None:
    """Verify every static tr(...) key used across src/semsearch/ and MatchReason values exist in catalogs."""
    src_root = Path(__file__).parent.parent / "src" / "semsearch"
    used_keys: set[str] = set()
    for py_file in src_root.rglob("*.py"):
        if "i18n" in py_file.parts:
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "tr"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                used_keys.add(node.args[0].value)

    used_keys.update(reason.value for reason in MatchReason)
    catalog_keys = set(load_catalog("es").keys())
    missing = used_keys - catalog_keys
    assert not missing, f"Keys used in src/semsearch/ missing from translation catalogs: {sorted(missing)}"


@pytest.mark.parametrize("lang_code", sorted(EXPECTED_TRANSLATION_LANGUAGES))
def test_translation_catalog_completeness_and_placeholders(lang_code: str) -> None:
    """Verify every JSON language catalog has identical keys and preserves format placeholders."""
    reference_keys = set(load_catalog("es").keys())
    catalog = load_catalog(lang_code)
    missing_keys = reference_keys - set(catalog.keys())
    extra_keys = set(catalog.keys()) - reference_keys
    assert not missing_keys, f"Language '{lang_code}' is missing keys: {missing_keys}"
    assert not extra_keys, f"Language '{lang_code}' has unexpected keys: {extra_keys}"

    for source_key, translated_val in catalog.items():
        assert isinstance(translated_val, str)
        assert translated_val.strip(), f"Empty translation for key '{source_key}' in '{lang_code}'"
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
    """Verify locale detection, colon-separated LANGUAGE fallback, and ISO aliases."""
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.delenv("LANGUAGE", raising=False)

    monkeypatch.setenv("LANG", "es_ES.UTF-8")
    assert detect_system_language() == "es"
    assert set_language("auto") == "es"
    assert tr("Quick Search") != "Quick Search"

    # Colon-separated LANGUAGE chooses first supported language
    monkeypatch.setenv("LANGUAGE", "ca:es:en")
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    assert normalize_language_code("ca:es:en") == "es"
    assert detect_system_language() == "es"

    # Explicit English in LANGUAGE stops before falling back to LANG
    monkeypatch.setenv("LANGUAGE", "ca:en:es")
    assert detect_system_language() == "en"

    # Unsupported LANGUAGE entries fall through to LANG before defaulting to 'en'
    monkeypatch.setenv("LANGUAGE", "zu:eo")
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    assert detect_system_language() == "fr"

    monkeypatch.setenv("LANG", "sw_KE.UTF-8")
    assert detect_system_language() == "en"

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
    del qapp
    mock_search = MagicMock()
    mock_search.search.return_value = ResultSet()
    mock_ollama = MagicMock()
    mock_ollama.list_multimodal_models.return_value = ["embeddinggemma-2"]

    cfg_mgr = ConfigManager(config_path=tmp_path / "config.json")

    es_catalog = load_catalog("es")
    ru_catalog = load_catalog("ru")
    ar_catalog = load_catalog("ar")

    set_language("es")
    tray = SystemTrayManager()
    panel = SearchPanel(mock_search, AppConfig())
    preview = PreviewPane()
    settings = SettingsDialog(cfg_mgr, mock_ollama)
    wizard = OnboardingWizard(cfg_mgr, mock_ollama)

    footer_key = "↵ Open    Shift+↵ Show in Files    Ctrl+C Copy    Tab Actions    Esc Dismiss"
    assert tray.quit_action.text() == es_catalog["Quit"]
    assert panel.footer_hint.text() == es_catalog[footer_key]
    assert settings.windowTitle() == es_catalog["SemSearch Settings"]
    assert wizard.welcome_page.title() == es_catalog["Welcome to SemSearch"]

    # Switch to Russian dynamically and retranslate
    set_language("ru")
    tray.retranslate_ui()
    panel.retranslate_ui()
    preview.retranslate_ui()
    settings.retranslate_ui()

    assert tray.quit_action.text() == ru_catalog["Quit"]
    assert panel.footer_hint.text() == ru_catalog[footer_key]
    assert preview.empty_view.text_label.text() == ru_catalog["Select an item to preview"]
    assert settings.windowTitle() == ru_catalog["SemSearch Settings"]

    # Verify status formatting with placeholders in Arabic
    set_language("ar")
    tray.update_status(EngineStatus(activity=EngineActivity.INDEXING, indexed=25, total=50))
    expected_ar = tr("Indexing: {percent}% ({indexed}/{total})", percent=50, indexed=25, total=50)
    assert tray.status_action.text() == expected_ar

    # Verify SearchHit.reason stays domain-pure and translates cleanly via tr()
    hit = SearchHit(path="/tmp/a.pdf", kind=FileKind.PDF, score=1.0, z=2.5)
    assert hit.reason is MatchReason.MEANING
    assert tr(hit.reason) == ar_catalog["Matches meaning"]
    parser = build_parser()
    assert parser.description == ar_catalog["SemSearch - Linux Desktop Semantic Search Engine"]
