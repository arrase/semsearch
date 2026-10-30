"""Text normalization, term extraction, and keyword search utilities matching DigUp SearchText."""

import re
import unicodedata

from semsearch.core.types import FileKind

_STRIP_SCALARS = {
    0x00AD,
    0x061C,
    0xFEFF,
    0x0640,  # Tatweel
}
# Ranges of invisible formatting / direction / joiner characters
_STRIP_RANGES = [
    (0x200C, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x2064),
    (0x2066, 0x2069),
    (0x064B, 0x065F),  # Harakat
]

_ALEF_VARIANTS = {0x0622, 0x0623, 0x0625, 0x0671}
_ZERO_WIDTH_SPACE = 0x200B
_SUPERSCRIPT_ALEF = 0x0670
_INDIC_START = 0x0900
_INDIC_END = 0x0DFF
_ARABIC_START = 0x0600
_ARABIC_END = 0x06FF
_COMBINING_MARK_START = 0x0300
_COMBINING_MARK_END = 0x036F
_MIN_TERM_LEN = 2
_MIN_HINT_PREFIX_LEN = 3
_MIN_LOOKUP_LEN = 4
_MAX_TERM_MATCHES = 500

STOPWORDS: set[str] = {
    "a",
    "an",
    "the",
    "of",
    "in",
    "on",
    "at",
    "to",
    "for",
    "with",
    "and",
    "or",
    "my",
    "me",
    "that",
    "this",
    "these",
    "those",
    "from",
    "about",
    "where",
    "what",
    "which",
    "who",
    "when",
    "is",
    "was",
    "are",
    "were",
    "be",
    "it",
    "its",
    "some",
    "any",
    "find",
    "show",
    "file",
    "files",
    "photo",
    "photos",
    "picture",
    "pictures",
    "image",
    "images",
    "screenshot",
    "screenshots",
    "video",
    "videos",
    "pdf",
    "pdfs",
    "document",
    "documents",
    "doc",
    "docs",
    "note",
    "notes",
    "paper",
    "papers",
    "clip",
    "movie",
    "audio",
    "song",
    "podcast",
    "voice",
    "sound",
    "recording",
    # Spanish stopwords and kind words
    "de",
    "la",
    "el",
    "en",
    "con",
    "para",
    "por",
    "del",
    "al",
    "los",
    "las",
    "un",
    "una",
    "unos",
    "unas",
    "mi",
    "mis",
    "su",
    "sus",
    "que",
    "como",
    "donde",
    "cuando",
    "quien",
    "cual",
    "es",
    "son",
    "fue",
    "era",
    "este",
    "esta",
    "estos",
    "estas",
    "ese",
    "esa",
    "esos",
    "esas",
    "busca",
    "buscar",
    "mostrar",
    "muestra",
    "archivo",
    "archivos",
    "foto",
    "fotos",
    "imagen",
    "imagenes",
    "imágenes",
    "captura",
    "capturas",
    "pantalla",
    "vídeo",
    "vídeos",
    "documento",
    "documentos",
    "nota",
    "notas",
    "audios",
    "cancion",
    "canción",
    "grabacion",
    "grabación",
    "codigo",
    "código",
    # Bengali and Arabic stopwords
    "এবং",
    "ও",
    "এই",
    "সেই",
    "একটি",
    "আমার",
    "থেকে",
    "জন্য",
    "সাথে",
    "মধ্যে",
    "কোথায়",
    "কোন",
    "কী",
    "কি",
    "যে",
    "ছবি",
    "স্ক্রিনশট",
    "ভিডিও",
    "ফাইল",
    "পিডিএফ",
    "في",
    "من",
    "على",
    "الى",
    "عن",
    "مع",
    "هذا",
    "هذه",
    "ذلك",
    "التي",
    "الذي",
    "اين",
    "ما",
    "صورة",
    "صور",
    "لقطة",
    "شاشة",
    "فيديو",
    "ملف",
    "مستند",
}

KIND_WORDS: dict[str, FileKind] = {
    "screenshot": FileKind.SCREENSHOT,
    "screenshots": FileKind.SCREENSHOT,
    "photo": FileKind.IMAGE,
    "photos": FileKind.IMAGE,
    "picture": FileKind.IMAGE,
    "pictures": FileKind.IMAGE,
    "image": FileKind.IMAGE,
    "images": FileKind.IMAGE,
    "pdf": FileKind.PDF,
    "pdfs": FileKind.PDF,
    "paper": FileKind.PDF,
    "papers": FileKind.PDF,
    "document": FileKind.DOC,
    "documents": FileKind.DOC,
    "note": FileKind.DOC,
    "notes": FileKind.DOC,
    "doc": FileKind.DOC,
    "docs": FileKind.DOC,
    "video": FileKind.VIDEO,
    "videos": FileKind.VIDEO,
    "clip": FileKind.VIDEO,
    "movie": FileKind.VIDEO,
    "audio": FileKind.AUDIO,
    "song": FileKind.AUDIO,
    "podcast": FileKind.AUDIO,
    "voice": FileKind.AUDIO,
    "sound": FileKind.AUDIO,
    "code": FileKind.CODE,
    # Spanish kind words
    "captura": FileKind.SCREENSHOT,
    "capturas": FileKind.SCREENSHOT,
    "pantalla": FileKind.SCREENSHOT,
    "foto": FileKind.IMAGE,
    "fotos": FileKind.IMAGE,
    "imagen": FileKind.IMAGE,
    "imagenes": FileKind.IMAGE,
    "imágenes": FileKind.IMAGE,
    "documento": FileKind.DOC,
    "documentos": FileKind.DOC,
    "nota": FileKind.DOC,
    "notas": FileKind.DOC,
    "vídeo": FileKind.VIDEO,
    "vídeos": FileKind.VIDEO,
    "pelicula": FileKind.VIDEO,
    "película": FileKind.VIDEO,
    "audios": FileKind.AUDIO,
    "cancion": FileKind.AUDIO,
    "canción": FileKind.AUDIO,
    "grabacion": FileKind.AUDIO,
    "grabación": FileKind.AUDIO,
    "voz": FileKind.AUDIO,
    "sonido": FileKind.AUDIO,
    "codigo": FileKind.CODE,
    "código": FileKind.CODE,
    # Non-Latin hints
    "স্ক্রিনশট": FileKind.SCREENSHOT,
    "ছবি": FileKind.IMAGE,
    "পিডিএফ": FileKind.PDF,
    "নোট": FileKind.DOC,
    "ভিডিও": FileKind.VIDEO,
    "অডিও": FileKind.AUDIO,
    "গান": FileKind.AUDIO,
    "রেকর্ডিং": FileKind.AUDIO,
    "لقطة": FileKind.SCREENSHOT,
    "صورة": FileKind.IMAGE,
    "صور": FileKind.IMAGE,
    "مستند": FileKind.DOC,
    "ملاحظات": FileKind.DOC,
    "فيديو": FileKind.VIDEO,
    "مقطع": FileKind.VIDEO,
    "صوت": FileKind.AUDIO,
    "تسجيل": FileKind.AUDIO,
    "اغنية": FileKind.AUDIO,
}


def _is_word_char(char: str) -> bool:
    cat = unicodedata.category(char)
    return cat[0] in ("L", "N", "M")


def _tokenize_words(text: str) -> list[str]:
    words: list[str] = []
    current: list[str] = []
    for c in text:
        if _is_word_char(c):
            current.append(c)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words


def normalize_search_text(text: str) -> str:
    """Normalize text using NFKC, strip invisible marks/harakat, and normalize alef."""
    compatible = unicodedata.normalize("NFKC", text)
    result: list[str] = []

    for char in compatible:
        val = ord(char)
        if val == _ZERO_WIDTH_SPACE:
            result.append(" ")
            continue
        if val in _STRIP_SCALARS:
            continue
        if any(start <= val <= end for start, end in _STRIP_RANGES):
            continue
        if val == _SUPERSCRIPT_ALEF:
            continue
        if val in _ALEF_VARIANTS:
            result.append("\u0627")
            continue
        result.append(char)

    return "".join(result)


def inflects(word: str) -> bool:
    """Check if word belongs to scripts that attach endings (Indic or Arabic)."""
    for char in word:
        val = ord(char)
        if _INDIC_START <= val <= _INDIC_END or _ARABIC_START <= val <= _ARABIC_END:
            return True
    return False


def has_words(text: str) -> bool:
    """Check whether text has words: at least a fifth of non-whitespace characters are letters."""
    letters = 0
    others = 0
    for char in text:
        if char.isspace():
            continue
        if unicodedata.category(char)[0] in ("L", "M"):
            letters += 1
        else:
            others += 1
    return letters > 0 and letters * 4 >= others


def extract_terms(text: str) -> list[str]:
    """Extract content words of query: normalized, lowercase, without stopwords or kind words."""
    norm = normalize_search_text(text).lower()
    raw_tokens = _tokenize_words(norm)
    seen: set[str] = set()
    terms: list[str] = []
    for token in raw_tokens:
        clean = token.strip()
        if len(clean) >= _MIN_TERM_LEN and clean not in STOPWORDS and clean not in seen:
            seen.add(clean)
            terms.append(clean)
    return terms


def make_fts_patterns(terms: list[str], *, prefix_last: bool = False) -> list[str]:
    """Generate quoted FTS5 patterns with selective prefix wildcards."""
    patterns: list[str] = []
    for idx, term in enumerate(terms):
        prefix = (prefix_last and idx == len(terms) - 1) or inflects(term)
        patterns.append(f'"{term}"*' if prefix else f'"{term}"')
    return patterns


def is_lookup(terms: list[str]) -> bool:
    """Determine if query terms represent codes, IDs, or model identifiers."""
    if not terms:
        return False
    return all(
        any(c.isdigit() for c in t) and (any(c.isalpha() for c in t) or len(t) >= _MIN_LOOKUP_LEN) for t in terms
    )


def is_lookup_query(query: str, terms: list[str] | None = None) -> bool:
    """Determine whether the query is an identifier, symbol, path, or serial lookup."""
    q = query.strip()
    if not q:
        return False
    if terms and is_lookup(terms):
        return True
    if re.search(r"[a-z][A-Z]", q) or re.search(r"[a-zA-Z0-9]_[a-zA-Z0-9]", q):
        return True
    if "/" in q or "\\" in q or "::" in q or re.search(r"\w\.\w", q):
        return True
    if re.search(r"\b[A-Za-z0-9]+-[A-Za-z0-9]+\b", q) or re.search(r"\b0x[0-9a-fA-F]+\b", q):
        return True
    words = re.findall(r"\w+", q)
    return any(re.search(r"\d", w) and re.search(r"[a-zA-Z]", w) for w in words)


def extract_kind_hints(text: str) -> set[FileKind]:
    """Identify soft file kind hints from words in the query."""
    norm = normalize_search_text(text).lower()
    words = _tokenize_words(norm)
    hints: set[FileKind] = set()
    for w in words:
        if w in KIND_WORDS:
            hints.add(KIND_WORDS[w])
        else:
            for kw, kind in KIND_WORDS.items():
                if inflects(kw) and len(kw) >= _MIN_HINT_PREFIX_LEN and w.startswith(kw):
                    hints.add(kind)
                    break
    return hints


def _fold_latin_diacritics(text: str) -> str:
    """Normalize text and strip Latin combining diacritical marks (0x0300..0x036F)."""
    norm = normalize_search_text(text).lower()
    return "".join(
        ch for ch in unicodedata.normalize("NFD", norm) if not (_COMBINING_MARK_START <= ord(ch) <= _COMBINING_MARK_END)
    )


def _resolve_match_bounds(text: str, orig_start: int, orig_end: int, *, prefix: bool) -> tuple[int, int] | None:
    if orig_start > 0 and _is_word_char(text[orig_start - 1]):
        return None
    if orig_end < len(text) and _is_word_char(text[orig_end]):
        if not prefix:
            return None
        while orig_end < len(text) and _is_word_char(text[orig_end]):
            orig_end += 1
    return orig_start, orig_end


def find_term_matches(word: str, text: str) -> list[tuple[int, int]]:
    """Locate occurrences of word in text with boundary checks, diacritic folding, and suffix extension."""
    needle = _fold_latin_diacritics(word)
    if not needle:
        return []
    prefix = inflects(word)
    norm_chars: list[str] = []
    origin: list[int] = []
    for idx, c in enumerate(text):
        p = _fold_latin_diacritics(c)
        norm_chars.append(p)
        origin.extend([idx] * len(p))
    haystack = "".join(norm_chars)

    found: list[tuple[int, int]] = []
    cursor = 0
    while len(found) < _MAX_TERM_MATCHES:
        pos = haystack.find(needle, cursor)
        if pos == -1:
            break
        hit_end = pos + len(needle)
        cursor = hit_end
        if not origin or hit_end > len(origin):
            continue
        bounds = _resolve_match_bounds(text, origin[pos], origin[hit_end - 1] + 1, prefix=prefix)
        if bounds is not None:
            found.append(bounds)
    return found


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    ranges.sort()
    merged: list[tuple[int, int]] = []
    for r in ranges:
        if not merged or r[0] >= merged[-1][1]:
            merged.append(r)
        else:
            last = merged[-1]
            merged[-1] = (last[0], max(last[1], r[1]))
    return merged


def extract_snippet(text: str, terms: list[str], max_words: int = 12) -> str | None:
    """Extract snippet around the first matched term highlighted with « and » markers."""
    if not text or not terms:
        return None

    clean_terms = [t.strip() for t in terms if len(t.strip()) >= _MIN_TERM_LEN]
    if not clean_terms:
        return None

    all_ranges: list[tuple[int, int]] = []
    for term in clean_terms:
        all_ranges.extend(find_term_matches(term, text))

    if not all_ranges:
        return None

    merged = _merge_ranges(all_ranges)
    first_start, _ = merged[0]
    words = list(re.finditer(r"\S+", text))
    if not words:
        return None

    target_idx = next(
        (idx for idx, w in enumerate(words) if w.start() <= first_start < w.end() or w.start() >= first_start),
        0,
    )

    start_word_idx = max(0, target_idx - max_words // 3)
    end_word_idx = min(len(words), start_word_idx + max_words)

    snippet_start = words[start_word_idx].start()
    snippet_end = words[end_word_idx - 1].end()

    prefix = "… " if start_word_idx > 0 else ""
    suffix = " …" if end_word_idx < len(words) else ""

    out_parts: list[str] = [prefix]
    cursor = snippet_start
    for r_start, r_end in merged:
        if r_start >= snippet_start and r_end <= snippet_end and r_start >= cursor:
            out_parts.extend((text[cursor:r_start], "«", text[r_start:r_end], "»"))
            cursor = r_end

    out_parts.extend((text[cursor:snippet_end], suffix))

    raw_snippet = "".join(out_parts)
    return re.sub(r"\s+", " ", raw_snippet).strip()
