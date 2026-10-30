"""Code symbol lookup, definition detection, and line focusing matching DigUp CodeLookup."""

import re

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*(?:(?:\.|::|->|#)[A-Za-z_$][A-Za-z0-9_$]*)*$")

_DEFINERS: set[str] = {
    "def",
    "func",
    "fn",
    "fun",
    "function",
    "class",
    "struct",
    "enum",
    "protocol",
    "interface",
    "trait",
    "type",
    "typealias",
    "actor",
    "extension",
    "impl",
    "module",
    "namespace",
    "object",
    "record",
    "macro",
    "const",
    "let",
    "var",
    "val",
    "union",
    "mod",
    "sub",
    "@interface",
    "@implementation",
    "@protocol",
    "#define",
    "function*",
}

_USERS: set[str] = {
    "return",
    "await",
    "yield",
    "throw",
    "new",
    "else",
    "case",
    "print",
    "echo",
    "assert",
    "not",
    "and",
    "or",
    "in",
    "is",
    "do",
    "go",
    "defer",
    "try",
    "catch",
    "if",
    "while",
    "for",
    "typeof",
    "delete",
    "raise",
    "import",
    "from",
}

_COMMENT_STARTS: tuple[str, ...] = ("//", "#", "/*", "*", "--", '"""', "'''", "<!--", ";")
_NON_VAR_DEFINERS: set[str] = _DEFINERS - {"let", "var", "const", "val"}
_STRONG_DEFINITION = 2
_MIN_WORD_LEN = 2
_MAX_SUMMARY_LEN = 160


def extract_identifier(query: str) -> list[str] | None:
    """Check if query is a code symbol/identifier and return its tokenized words."""
    clean = query.strip()
    if clean.endswith("()"):
        clean = clean[:-2].strip()

    if not _IDENTIFIER_RE.match(clean):
        return None

    has_upper = any(c.isupper() for c in clean[1:])
    has_lower = any(c.islower() for c in clean)
    is_camel = has_upper and has_lower
    has_symbols = any(c in "_.:#$->" or c.isdigit() for c in clean)

    if not (is_camel or has_symbols):
        return None

    words = [w for w in re.split(r"[^a-zA-Z0-9]+", clean.lower()) if w]
    return words or None


def identifier_phrase(words: list[str]) -> str:
    """Return quoted FTS phrase pattern so words match consecutively as written."""
    return f'"{" ".join(words)}"'


def _is_name_char(c: str) -> bool:
    return c.isalnum() or c in ("_", "$")


def _occurrences(name: str, line: str) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    line_lower = line.lower()
    name_lower = name.lower()
    cursor = 0
    while True:
        pos = line_lower.find(name_lower, cursor)
        if pos == -1:
            break
        end = pos + len(name_lower)
        cursor = end
        clear_before = pos == 0 or not _is_name_char(line[pos - 1])
        clear_after = end == len(line) or not _is_name_char(line[end])
        if clear_before and clear_after:
            found.append((pos, end))
    return found


def _is_c_like_signature(before: str, after: str, previous: str) -> bool:
    if not after.startswith("(") or not before or previous in _USERS or "->" in before:
        return False
    if any(c in "=(,.!?" for c in before):
        return False
    last = before[-1]
    return last.isalnum() or last in "*&>_]"


def _definition_strength(before: str, after: str, line: str) -> int:
    before_tokens = before.split()
    previous = before_tokens[-1] if before_tokens else ""

    if previous in _NON_VAR_DEFINERS:
        return 2

    if before.startswith("func (") and before.endswith(")"):
        return 2

    if before.startswith(("- (", "+ (")):
        return 2 if (not after or after.startswith((":", "{", ";"))) else 0

    if _is_c_like_signature(before, after, previous):
        return 1 if line.strip().endswith(";") else 2

    return 0


def find_definition(name: str, text: str, first_line: int = 1) -> tuple[int, int] | None:
    """Find line number and confidence strength (1 or 2) where name is defined."""
    clean_name = name.strip()
    if clean_name.endswith("()"):
        clean_name = clean_name[:-2].strip()

    best: tuple[int, int] | None = None

    for offset, line in enumerate(text.splitlines()):
        for start, end in _occurrences(clean_name, line):
            before = line[:start].strip()
            after = line[end:].strip()
            strength = _definition_strength(before, after, line)
            if strength > (best[1] if best else 0):
                best = (first_line + offset, strength)
            if strength == _STRONG_DEFINITION:
                return best

    return best


def _has_word_part(word: str, line: str) -> bool:
    line_lower = line.lower()
    word_lower = word.lower()
    cursor = 0
    while True:
        pos = line_lower.find(word_lower, cursor)
        if pos == -1:
            break
        cursor = pos + len(word_lower)
        if pos == 0:
            return True
        prev = line[pos - 1]
        if not (prev.isalpha() or prev.isdigit()):
            return True
        if line[pos].isupper():
            return True
    return False


def find_first_line_with_words(words: list[str], text: str, first_line: int = 1) -> int | None:
    """Find first relevant line containing query words, prioritizing definitions over comments."""
    clean_words = [w.lower() for w in words if len(w) >= _MIN_WORD_LEN]
    if not clean_words:
        return None

    first_code: int | None = None
    first_any: int | None = None

    for offset, line in enumerate(text.splitlines()):
        if not any(_has_word_part(w, line) for w in clean_words):
            continue
        stripped = line.strip()
        if any(t in _NON_VAR_DEFINERS for t in stripped.split()[:4]):
            return first_line + offset

        if first_code is None and not stripped.startswith(_COMMENT_STARTS):
            first_code = offset
        if first_any is None:
            first_any = offset

    res = first_code if first_code is not None else first_any
    return (first_line + res) if res is not None else None


_SUMMARY_IGNORE_PREFIXES = (
    "//",
    "#",
    "/*",
    "*",
    "--",
    '"""',
    "'''",
    "import ",
    "from ",
    "#include",
    "using ",
    "package ",
    "}",
    ")",
    "@",
)


def code_summary(text: str) -> str:
    """Extract a single-line summary of a code stretch for result lists."""
    clean_lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not clean_lines:
        return ""

    definition_line: str | None = None
    first_code_line: str | None = None

    for line in clean_lines:
        tokens = line.split()[:4]
        if any(t in _DEFINERS and t not in ("let", "var", "const", "val") for t in tokens):
            definition_line = line
            break
        if first_code_line is None and not any(line.startswith(p) for p in _SUMMARY_IGNORE_PREFIXES):
            first_code_line = line

    line = definition_line or first_code_line or clean_lines[0]
    return (line[:_MAX_SUMMARY_LEN] + "…") if len(line) > _MAX_SUMMARY_LEN else line
