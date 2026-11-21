"""Text normalization, term extraction, query filter parsing, and keyword search utilities."""

import re
import time
import unicodedata
from datetime import UTC, datetime

from semsearch.core.types import FileKind, ParsedQuery

_STRIP_SCALARS = {
    0x00AD,
    0x061C,
    0xFEFF,
    0x0640,  # Tatweel
}
# Ranges of invisible formatting / direction / joiner characters
_STRIP_RANGES = (
    (0x200C, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x2064),
    (0x2066, 0x2069),
    (0x064B, 0x065F),  # Harakat
)

_ALEF_VARIANTS = {0x0622, 0x0623, 0x0625, 0x0671}
_ZERO_WIDTH_SPACE = 0x200B
_SUPERSCRIPT_ALEF = 0x0670
_INDIC_START = 0x0900
_INDIC_END = 0x0DFF
_ARABIC_START = 0x0600
_ARABIC_END = 0x06FF
_COMBINING_MARK_START = 0x0300
_COMBINING_MARK_END = 0x036F
_CJK_RANGES = (
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
    (0xAC00, 0xD7AF),  # Hangul Syllables
)
_EXTENDED_LATIN_TABLE = str.maketrans(
    {
        "ł": "l",
        "ø": "o",
        "ß": "ss",
        "đ": "d",
        "æ": "ae",
        "œ": "oe",
    }
)
_CAMEL_BOUNDARY_1 = re.compile(r"([a-z])([A-Z])")
_CAMEL_BOUNDARY_2 = re.compile(r"([A-Z]+|[0-9]+)([A-Z][a-z])")
_FILTER_TOKEN_RE = re.compile(
    r'(?<!\S)(?P<key>kind|ext|path|after):(?:"(?P<dq>[^"]*)"|\'(?P<sq>[^\']*)\'|(?P<val>\S+))',
    re.IGNORECASE,
)
_CODE_TOKEN_RE = re.compile(r"(?<!\S)code:(?=\s|$)", re.IGNORECASE)
_RELATIVE_AFTER_RE = re.compile(r"^(\d+)([dwm])$", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_SECONDS_PER_DAY = 86400
_AFTER_UNIT_SECONDS = {
    "d": _SECONDS_PER_DAY,
    "w": 7 * _SECONDS_PER_DAY,
    "m": 30 * _SECONDS_PER_DAY,
}
_FILTER_KIND_ALIASES: dict[str, FileKind] = {
    "pdf": FileKind.PDF,
    "doc": FileKind.DOC,
    "docs": FileKind.DOC,
    "image": FileKind.IMAGE,
    "img": FileKind.IMAGE,
    "photo": FileKind.IMAGE,
    "screenshot": FileKind.SCREENSHOT,
    "shot": FileKind.SCREENSHOT,
    "audio": FileKind.AUDIO,
    "video": FileKind.VIDEO,
    "code": FileKind.CODE,
}

_MIN_TERM_LEN = 2
_MIN_HINT_PREFIX_LEN = 3
_MIN_LOOKUP_LEN = 4
_MAX_TERM_MATCHES = 500
_ASCII_LIMIT = 128

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
    "code",
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


def _is_cjk_char(ch: str) -> bool:
    """Return True if character lies in a CJK ideograph, Kana, or Hangul Unicode range."""
    val = ord(ch)
    return any(start <= val <= end for start, end in _CJK_RANGES)


def _is_word_char(char: str) -> bool:
    cat = unicodedata.category(char)
    return cat[0] in ("L", "N", "M")


def _is_valid_term(term: str) -> bool:
    return len(term) >= _MIN_TERM_LEN or (len(term) == 1 and _is_cjk_char(term))


def split_camel_case(text: str) -> str:
    """Insert spaces at camelCase and PascalCase word boundaries."""
    step = _CAMEL_BOUNDARY_2.sub(r"\1 \2", text)
    return _CAMEL_BOUNDARY_1.sub(r"\1 \2", step)


def _normalize_chars(text: str) -> str:
    """Apply NFKC normalization, strip invisible marks/harakat, and normalize alef variants."""
    compatible = unicodedata.normalize("NFKC", text)
    result: list[str] = []

    for char in compatible:
        val = ord(char)
        if val == _ZERO_WIDTH_SPACE:
            result.append(" ")
            continue
        if val in _STRIP_SCALARS or val == _SUPERSCRIPT_ALEF:
            continue
        if any(start <= val <= end for start, end in _STRIP_RANGES):
            continue
        if val in _ALEF_VARIANTS:
            result.append("\u0627")
            continue
        result.append(char)

    return "".join(result)


def _expand_cjk_run(run: str) -> str:
    if len(run) == 1:
        return run
    tokens = list(run)
    tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return " ".join(tokens)


def _expand_cjk_runs(text: str) -> str:
    out: list[str] = []
    cjk_buf: list[str] = []
    for ch in text:
        if _is_cjk_char(ch):
            if not cjk_buf and out and not out[-1].isspace():
                out.append(" ")
            cjk_buf.append(ch)
            continue
        if cjk_buf:
            out.append(_expand_cjk_run("".join(cjk_buf)))
            cjk_buf = []
            if not ch.isspace():
                out.append(" ")
        out.append(ch)
    if cjk_buf:
        out.append(_expand_cjk_run("".join(cjk_buf)))
    return "".join(out)


def _tokenize_words(text: str) -> list[str]:
    words: list[str] = []
    current: list[str] = []
    cjk_run: list[str] = []

    def flush_current() -> None:
        if current:
            words.append("".join(current))
            current.clear()

    def flush_cjk() -> None:
        if not cjk_run:
            return
        run = "".join(cjk_run)
        cjk_run.clear()
        if len(run) == 1:
            words.append(run)
        else:
            words.extend(run[i : i + 2] for i in range(len(run) - 1))

    for ch in text:
        if _is_cjk_char(ch):
            flush_current()
            cjk_run.append(ch)
        elif _is_word_char(ch):
            flush_cjk()
            current.append(ch)
        else:
            flush_current()
            flush_cjk()

    flush_current()
    flush_cjk()
    return words


def normalize_search_text(text: str) -> str:
    """Normalize text for FTS indexing with NFKC, camelCase splitting, and CJK unigram/bigram expansion."""
    normalized = split_camel_case(_normalize_chars(text))
    if any(_is_cjk_char(ch) for ch in normalized):
        return _expand_cjk_runs(normalized)
    return normalized


def _parse_kind_filter(value: str) -> set[FileKind] | None:
    parts = [part.strip().lower() for part in value.split(",") if part.strip()]
    if not parts or any(part not in _FILTER_KIND_ALIASES for part in parts):
        return None
    return {_FILTER_KIND_ALIASES[part] for part in parts}


def _parse_ext_filter(value: str) -> set[str] | None:
    parts = [part.strip().lstrip(".").lower() for part in value.split(",")]
    cleaned = {part for part in parts if part}
    return cleaned or None


def _parse_after_filter(value: str, now_ts: float) -> float | None:
    rel_match = _RELATIVE_AFTER_RE.match(value)
    if rel_match:
        amount = int(rel_match.group(1))
        unit = rel_match.group(2).lower()
        return max(0.0, now_ts - amount * _AFTER_UNIT_SECONDS[unit])

    if _ISO_DATE_RE.match(value):
        try:
            dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            return None
        return dt.timestamp()

    return None


def _match_filter_value(match: re.Match[str]) -> str:
    if match.group("dq") is not None:
        return match.group("dq")
    if match.group("sq") is not None:
        return match.group("sq")
    return match.group("val")


def _apply_filter_match(
    key: str,
    val: str,
    *,
    reference_now: float,
    kinds: set[FileKind],
    extensions: set[str],
    path_fragments: list[str],
    after_mtime: float,
) -> tuple[bool, float]:
    consumed = False
    if key == "kind":
        parsed_kinds = _parse_kind_filter(val)
        if parsed_kinds is not None:
            kinds.update(parsed_kinds)
            consumed = True
    elif key == "ext":
        parsed_exts = _parse_ext_filter(val)
        if parsed_exts is not None:
            extensions.update(parsed_exts)
            consumed = True
    elif key == "path":
        frag = val.strip()
        if frag:
            path_fragments.append(frag)
            consumed = True
    else:
        parsed_ts = _parse_after_filter(val.strip(), reference_now)
        if parsed_ts is not None:
            after_mtime = max(after_mtime, parsed_ts)
            consumed = True
    return consumed, after_mtime


def parse_query_filters(raw_query: str, *, now_ts: float | None = None) -> ParsedQuery:
    """Extract inline filters (code:, kind:, ext:, path:, after:) from a raw user query."""
    text = raw_query.strip()
    is_code = False
    if text.lower().startswith("code:"):
        is_code = True
        text = text[5:].lstrip()

    reference_now = time.time() if now_ts is None else now_ts
    kinds: set[FileKind] = set()
    extensions: set[str] = set()
    path_fragments: list[str] = []
    after_mtime = 0.0

    def consume_filter(match: re.Match[str]) -> str:
        nonlocal after_mtime
        consumed, after_mtime = _apply_filter_match(
            match.group("key").lower(),
            _match_filter_value(match),
            reference_now=reference_now,
            kinds=kinds,
            extensions=extensions,
            path_fragments=path_fragments,
            after_mtime=after_mtime,
        )
        return " " if consumed else match.group(0)

    remaining = _FILTER_TOKEN_RE.sub(consume_filter, text)
    if _CODE_TOKEN_RE.search(remaining):
        is_code = True
        remaining = _CODE_TOKEN_RE.sub(" ", remaining)

    clean_query = " ".join(remaining.split())
    return ParsedQuery(
        clean_query=clean_query,
        is_code=is_code,
        kinds=frozenset(kinds),
        extensions=frozenset(extensions),
        path_fragments=tuple(path_fragments),
        after_mtime=after_mtime,
    )


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
    norm = split_camel_case(_normalize_chars(text)).lower()
    raw_tokens = _tokenize_words(norm)
    seen: set[str] = set()
    terms: list[str] = []
    for token in raw_tokens:
        clean = token.strip()
        if _is_valid_term(clean) and clean not in STOPWORDS and clean not in seen:
            seen.add(clean)
            terms.append(clean)
    return terms


def make_fts_patterns(terms: list[str], *, prefix_last: bool = False) -> list[str]:
    """Generate quoted FTS5 patterns with escaped double quotes and selective prefix wildcards."""
    patterns: list[str] = []
    for idx, term in enumerate(terms):
        escaped = term.replace('"', '""')
        prefix = (prefix_last and idx == len(terms) - 1) or inflects(term)
        patterns.append(f'"{escaped}"*' if prefix else f'"{escaped}"')
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
    norm = _normalize_chars(text).lower()
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
    """Normalize text, fold extended Latin letters, and strip combining diacritical marks."""
    norm = _normalize_chars(text).lower().translate(_EXTENDED_LATIN_TABLE)
    return "".join(
        ch for ch in unicodedata.normalize("NFD", norm) if not (_COMBINING_MARK_START <= ord(ch) <= _COMBINING_MARK_END)
    )


def _build_folded_index(text: str) -> tuple[str, list[int]]:
    norm_chars: list[str] = []
    origin: list[int] = []
    for idx, ch in enumerate(text):
        if ord(ch) < _ASCII_LIMIT:
            norm_chars.append(ch.lower())
            origin.append(idx)
        else:
            folded = _fold_latin_diacritics(ch)
            norm_chars.append(folded)
            origin.extend([idx] * len(folded))
    return "".join(norm_chars), origin


def _resolve_match_bounds(
    text: str,
    orig_start: int,
    orig_end: int,
    *,
    prefix: bool,
    is_cjk: bool,
) -> tuple[int, int] | None:
    while orig_end < len(text) and unicodedata.category(text[orig_end])[0] == "M":
        orig_end += 1
    if is_cjk:
        return orig_start, orig_end
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
    folded_full = _fold_latin_diacritics(text)
    if needle not in folded_full:
        return []

    prefix = inflects(word)
    is_cjk = any(_is_cjk_char(ch) for ch in word)
    if text.isascii():
        haystack = text.lower()
        origin: list[int] | None = None
    else:
        haystack, origin = _build_folded_index(text)

    found: list[tuple[int, int]] = []
    cursor = 0
    while len(found) < _MAX_TERM_MATCHES:
        pos = haystack.find(needle, cursor)
        if pos == -1:
            break
        hit_end = pos + len(needle)
        if origin is None:
            orig_start, orig_end = pos, hit_end
        else:
            if hit_end > len(origin):
                cursor = pos + 1
                continue
            orig_start, orig_end = origin[pos], origin[hit_end - 1] + 1
        bounds = _resolve_match_bounds(text, orig_start, orig_end, prefix=prefix, is_cjk=is_cjk)
        if bounds is not None:
            found.append(bounds)
            cursor = hit_end
        else:
            cursor = pos + 1
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

    clean_terms = [stripped for t in terms if _is_valid_term(stripped := t.strip())]
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
