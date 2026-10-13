"""Internationalization (i18n) engine, locale detection, and translation catalog."""

import locale
import os

from semsearch.i18n import (
    ar,
    be,
    bg,
    de,
    es,
    fa,
    fr,
    he,
    id,
    it,
    kk,
    ms,
    nl,
    pl,
    pt,
    ru,
    sr,
    th,
    tl,
    tr as tr_lang,
    uk,
    vi,
)

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

TRANSLATIONS: dict[str, dict[str, str]] = {
    "es": es.STRINGS,
    "fr": fr.STRINGS,
    "de": de.STRINGS,
    "it": it.STRINGS,
    "pt": pt.STRINGS,
    "nl": nl.STRINGS,
    "pl": pl.STRINGS,
    "ar": ar.STRINGS,
    "tr": tr_lang.STRINGS,
    "fa": fa.STRINGS,
    "he": he.STRINGS,
    "id": id.STRINGS,
    "vi": vi.STRINGS,
    "th": th.STRINGS,
    "ms": ms.STRINGS,
    "tl": tl.STRINGS,
    "ru": ru.STRINGS,
    "uk": uk.STRINGS,
    "be": be.STRINGS,
    "bg": bg.STRINGS,
    "sr": sr.STRINGS,
    "kk": kk.STRINGS,
}

_current_language: str = DEFAULT_LANGUAGE


def normalize_language_code(raw_code: str) -> str:
    """Normalize a locale string (e.g. 'es_ES.UTF-8') to a supported language code or 'en'."""
    cleaned = raw_code.strip()
    if not cleaned:
        return DEFAULT_LANGUAGE
    cleaned = cleaned.split(":")[0].split(".")[0].split("@")[0]
    primary = cleaned.replace("-", "_").split("_")[0].lower()
    if primary in ("c", "posix", ""):
        return DEFAULT_LANGUAGE
    mapped = LANGUAGE_ALIASES.get(primary, primary)
    if mapped in SUPPORTED_LANGUAGES:
        return mapped
    return DEFAULT_LANGUAGE


def detect_system_language() -> str:
    """Detect active system locale and fall back to English ('en') if unsupported."""
    for env_var in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        val = os.environ.get(env_var, "").strip()
        if val and val.upper() not in ("C", "POSIX", "C.UTF-8"):
            return normalize_language_code(val)
        if val.upper() in ("C", "POSIX", "C.UTF-8"):
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
    if _current_language == DEFAULT_LANGUAGE:
        text = key
    else:
        text = TRANSLATIONS.get(_current_language, {}).get(key, key)
    if kwargs:
        return text.format(**kwargs)
    return text
