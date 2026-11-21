"""Unit tests for multilingual search text normalization, filters, CJK, and code lookup."""

from datetime import UTC, datetime

from semsearch.core.code_lookup import (
    code_summary,
    extract_identifier,
    find_definition,
)
from semsearch.core.search_text import (
    extract_kind_hints,
    extract_snippet,
    extract_terms,
    find_term_matches,
    make_fts_patterns,
    normalize_search_text,
    parse_query_filters,
)
from semsearch.core.types import FileKind


def test_spanish_stopwords_and_kind_hints() -> None:
    """Verify Spanish stopwords are stripped and Spanish kind words map to FileKind."""
    assert extract_terms("captura de pantalla") == []
    assert extract_kind_hints("captura de pantalla") == {FileKind.SCREENSHOT}

    assert extract_terms("información fiscal de juan ezquerro") == [
        "información",
        "fiscal",
        "juan",
        "ezquerro",
    ]
    assert extract_kind_hints("buscar fotos y videos de vacaciones") == {
        FileKind.IMAGE,
        FileKind.VIDEO,
    }
    assert extract_terms("find code for parser") == ["parser"]
    assert extract_terms("buscar código de parser") == ["parser"]


def test_diacritic_insensitive_term_matching_and_snippets() -> None:
    """Verify unaccented queries match and highlight accented and extended Latin text."""
    text = "Documento oficial de Autorización Juan Ezquerro e INFORMACIÓN FISCAL 2025."
    matches = find_term_matches("autorizacion", text)
    assert len(matches) == 1
    start, end = matches[0]
    assert text[start:end] == "Autorización"

    snippet = extract_snippet(text, ["autorizacion", "informacion"])
    assert snippet is not None
    assert "«Autorización»" in snippet
    assert "«INFORMACIÓN»" in snippet

    # Extended Latin folding (ł/ø/ß/đ/æ/œ)
    ext_text = "Miasto Łódź Hauptstraße Øresund Đorđe Æon Œuvre"
    for query, expected in [
        ("lodz", "Łódź"),
        ("hauptstrasse", "Hauptstraße"),
        ("oresund", "Øresund"),
        ("dorde", "Đorđe"),
        ("aeon", "Æon"),
        ("oeuvre", "Œuvre"),
    ]:
        ext_matches = find_term_matches(query, ext_text)
        assert len(ext_matches) == 1
        s, e = ext_matches[0]
        assert ext_text[s:e] == expected


def test_find_term_matches_overlapping_rejected_prefix() -> None:
    """Verify failed boundary match advances by pos + 1 rather than skipping overlapping valid match."""
    text = "xab-ab-a end"
    matches = find_term_matches("ab-a", text)
    assert len(matches) == 1
    s, e = matches[0]
    assert text[s:e] == "ab-a"


def test_make_fts_patterns_escapes_double_quotes() -> None:
    """Verify double quotes inside terms are escaped for FTS5."""
    assert make_fts_patterns(['say"hi'], prefix_last=False) == ['"say""hi"']
    assert make_fts_patterns(['say"hi'], prefix_last=True) == ['"say""hi"*']


def test_cjk_tokenization_and_normalization() -> None:
    """Verify 1-char CJK terms, bigrams, normalization, and snippet highlighting."""
    assert extract_terms("猫") == ["猫"]
    assert extract_terms("人工智能") == ["人工", "工智", "智能"]
    assert extract_terms("AI検索") == ["ai", "検索"]

    norm = normalize_search_text("人工智能")
    assert "人" in norm
    assert "工" in norm
    assert "人工" in norm
    assert "智能" in norm

    snippet = extract_snippet("这是人工智能搜索文档", ["智能"])
    assert snippet is not None
    assert "«智能»" in snippet


def test_camel_case_and_snake_case_normalization() -> None:
    """Verify camelCase/PascalCase splits into subwords matching snake_case bidirectionally."""
    assert normalize_search_text("OllamaClient") == "Ollama Client"
    assert normalize_search_text("HTTPServer") == "HTTP Server"
    assert normalize_search_text("getHTTPResponseCode") == "get HTTP Response Code"
    assert extract_terms("OllamaClient") == ["ollama", "client"]
    assert extract_terms("ollama_client") == ["ollama", "client"]


def test_parse_query_filters() -> None:
    """Verify parsing of code:, kind:, ext:, path:, and after: inline filters."""
    now_ts = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC).timestamp()

    parsed = parse_query_filters(
        'code: kind:pdf,docs kind:shot ext:.PDF path:"Mi Carpeta" after:7d budget report',
        now_ts=now_ts,
    )
    assert parsed.is_code is True
    assert parsed.clean_query == "budget report"
    assert parsed.kinds == frozenset({FileKind.PDF, FileKind.DOC, FileKind.SCREENSHOT})
    assert parsed.extensions == frozenset({"pdf"})
    assert parsed.path_fragments == ("Mi Carpeta",)
    assert parsed.after_mtime == now_ts - 7 * 86400

    parsed_weeks = parse_query_filters("kind:img,photo after:2w sunset", now_ts=now_ts)
    assert parsed_weeks.is_code is False
    assert parsed_weeks.kinds == frozenset({FileKind.IMAGE})
    assert parsed_weeks.after_mtime == now_ts - 14 * 86400
    assert parsed_weeks.clean_query == "sunset"

    parsed_months = parse_query_filters("after:3m notes", now_ts=now_ts)
    assert parsed_months.after_mtime == now_ts - 90 * 86400

    parsed_date = parse_query_filters("after:2025-01-15 invoice", now_ts=now_ts)
    expected_date_ts = datetime(2025, 1, 15, 0, 0, 0, tzinfo=UTC).timestamp()
    assert parsed_date.after_mtime == expected_date_ts
    assert parsed_date.clean_query == "invoice"

    parsed_invalid = parse_query_filters("kind:unknown after:not-a-date keep me", now_ts=now_ts)
    assert parsed_invalid.kinds == frozenset()
    assert parsed_invalid.after_mtime == 0.0
    assert parsed_invalid.clean_query == "kind:unknown after:not-a-date keep me"


def test_code_lookup_camel_qualified_and_arrow_functions() -> None:
    """Verify camelCase identifier splitting, qualified definitions, and JS/TS arrow definitions."""
    assert extract_identifier("OllamaClient") == ["ollama", "client"]
    assert extract_identifier("HTTPServer") == ["http", "server"]
    assert extract_identifier("retryWithBackoff()") == ["retry", "with", "backoff"]
    assert extract_identifier("Database.fts_search") == ["database", "fts", "search"]

    py_src = "class Database:\n    def fts_search(self, query: str) -> list:\n        return []\n"
    assert find_definition("Database.fts_search", py_src) == (2, 2)

    cpp_src = "namespace ns {\nvoid func(int x) {\n}\n}\n"
    assert find_definition("ns::func", cpp_src) == (2, 2)

    ts_src = (
        "const value = 42;\n"
        "export const SearchBar = (props: Props) => {\n"
        "  return null;\n"
        "};\n"
        "const helper = function(a: number) {\n"
        "  return a;\n"
        "};\n"
    )
    assert find_definition("value", ts_src) is None
    assert find_definition("SearchBar", ts_src) == (2, 2)
    assert find_definition("helper", ts_src) == (5, 2)

    summary = code_summary("// comment\nconst x = 1\nexport function render() {}\n")
    assert summary == "export function render() {}"
