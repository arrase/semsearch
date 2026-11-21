"""Internationalization (i18n) engine, locale detection, and lazy JSON translation catalogs."""

import json
import locale
import os
from pathlib import Path

DEFAULT_LANGUAGE = "en"

RTL_LANGUAGES: frozenset[str] = frozenset({"ar", "fa", "he"})

SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "it": "Italiano",
    "pt": "Português",
    "nl": "Nederlands",
    "pl": "Polski",
    "ar": "العربية",
    "tr": "Türkçe",
    "fa": "فارسی",
    "he": "עברית",
    "id": "Bahasa Indonesia",
    "vi": "Tiếng Việt",
    "th": "ไทย",
    "ms": "Bahasa Melayu",
    "tl": "Tagalog",
    "ru": "Русский",
    "uk": "Українська",
    "be": "Беларуская",
    "bg": "Български",
    "sr": "Српски",
    "kk": "Қазақша",
}

LANGUAGE_ALIASES: dict[str, str] = {
    "fil": "tl",
    "in": "id",
    "iw": "he",
}

_LOCALES_DIR = Path(__file__).parent / "locales"
_loaded_catalogs: dict[str, dict[str, str]] = {}
_current_language: str = DEFAULT_LANGUAGE


def load_catalog(lang: str) -> dict[str, str]:
    """Lazily load and cache the JSON translation catalog for a supported non-English language."""
    if lang == DEFAULT_LANGUAGE or lang not in SUPPORTED_LANGUAGES:
        return {}
    cached = _loaded_catalogs.get(lang)
    if cached is not None:
        return cached
    catalog_path = _LOCALES_DIR / f"{lang}.json"
    catalog: dict[str, str] = json.loads(catalog_path.read_text(encoding="utf-8"))
    _loaded_catalogs[lang] = catalog
    return catalog


def _match_supported_language(raw_code: str) -> str:
    """Return the first supported language code in a locale/LANGUAGE string, or '' if none match."""
    for entry in raw_code.split(":"):
        cleaned = entry.strip().split(".")[0].split("@")[0]
        if not cleaned:
            continue
        primary = cleaned.replace("-", "_").split("_")[0].lower()
        if primary in ("c", "posix"):
            return DEFAULT_LANGUAGE
        mapped = LANGUAGE_ALIASES.get(primary, primary)
        if mapped in SUPPORTED_LANGUAGES:
            return mapped
    return ""


def normalize_language_code(raw_code: str) -> str:
    """Normalize a locale string (e.g. 'es_ES.UTF-8' or 'ca:es:en') to a supported language code or 'en'."""
    return _match_supported_language(raw_code) or DEFAULT_LANGUAGE


def detect_system_language() -> str:
    """Detect active system locale and fall back to English ('en') if unsupported."""
    for env_var in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        val = os.environ.get(env_var, "").strip()
        if not val:
            continue
        matched = _match_supported_language(val)
        if matched:
            return matched
        if env_var != "LANGUAGE":
            return DEFAULT_LANGUAGE

    loc_name = locale.getlocale()[0]
    if loc_name:
        return normalize_language_code(loc_name)
    return DEFAULT_LANGUAGE


def resolve_language(lang: str = "auto") -> str:
    """Resolve 'auto' or explicit language tag to a supported language code."""
    cleaned = lang.strip().lower()
    if not cleaned or cleaned == "auto":
        return detect_system_language()
    return normalize_language_code(cleaned)


def set_language(lang: str = "auto") -> str:
    """Set the active application language and return the resolved language code."""
    global _current_language
    _current_language = resolve_language(lang)
    return _current_language


def get_language() -> str:
    """Return the currently active language code."""
    return _current_language


def is_rtl(lang: str | None = None) -> bool:
    """Return True if the specified or active language uses Right-to-Left layout."""
    target = lang if lang is not None else _current_language
    return target in RTL_LANGUAGES


def tr(key: str, **kwargs: object) -> str:
    """Translate a UI string into the active language using inline English as fallback."""
    text = key if _current_language == DEFAULT_LANGUAGE else load_catalog(_current_language).get(key, key)
    if kwargs:
        return text.format(**kwargs)
    return text
