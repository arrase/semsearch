"""Unit tests for multilingual search text normalization, term extraction, and snippet matching."""

from semsearch.core.search_text import (
    extract_kind_hints,
    extract_snippet,
    extract_terms,
    find_term_matches,
)
from semsearch.core.types import FileKind


def test_spanish_stopwords_and_kind_hints():
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


def test_diacritic_insensitive_term_matching_and_snippets():
    """Verify unaccented queries match and highlight accented Spanish text."""
    text = "Documento oficial de Autorización Juan Ezquerro e INFORMACIÓN FISCAL 2025."
    matches = find_term_matches("autorizacion", text)
    assert len(matches) == 1
    start, end = matches[0]
    assert text[start:end] == "Autorización"

    snippet = extract_snippet(text, ["autorizacion", "informacion"])
    assert snippet is not None
    assert "«Autorización»" in snippet
    assert "«INFORMACIÓN»" in snippet
