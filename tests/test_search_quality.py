"""Comprehensive verification suite for DigUp parity search quality and indexing improvements."""

from pathlib import Path
import httpx
import numpy as np
import pytest

from semsearch.core.code_lookup import (
    extract_identifier,
    find_definition,
    find_first_line_with_words,
    identifier_phrase,
)
from semsearch.core.database import Database
from semsearch.core.ollama import OllamaClient, format_doc_prompt
from semsearch.core.search_text import (
    extract_kind_hints,
    extract_snippet,
    extract_terms,
    has_words,
    inflects,
    is_lookup,
    make_fts_patterns,
    normalize_search_text,
)
from semsearch.core.types import (
    Candidate,
    FileKind,
    Modality,
    SegmentKind,
)
from semsearch.core.vectors import DIM, float16_to_bytes, l2_normalize
from semsearch.services.indexer import IndexService, _code_place
from semsearch.services.searcher import ResultSet, SearchService


def test_search_text_normalization():
    # NFKC fullwidth to ASCII
    assert normalize_search_text("ＡＢＣ") == "ABC"

    # Soft hyphen (0x00AD) and BOM (0xFEFF) stripped
    assert normalize_search_text("Doc\u00adument\ufeff") == "Document"

    # Zero-width space (0x200B) converted to regular space
    assert normalize_search_text("Word1\u200bWord2") == "Word1 Word2"

    # Tatweel (0x0640) stripped
    assert normalize_search_text("تـــطـــويـــل") == "تطويل"

    # Alef variants normalized to bare alef (\u0627)
    assert normalize_search_text("\u0622\u0623\u0625\u0671") == "\u0627\u0627\u0627\u0627"


def test_inflects_scripts():
    # Indic script
    assert inflects("কুমির") is True
    # Arabic script
    assert inflects("مدرسة") is True
    # Latin
    assert inflects("computer") is False


def test_has_words_filter():
    # Regular text has words
    assert has_words("This is a standard paragraph of textual content.") is True
    # Data dump of mostly digits has no words
    assert has_words("12345 67890 12389 44321 00921 55678") is False
    # Empty or whitespace
    assert has_words("") is False
    assert has_words("   \n\t  ") is False


def test_term_extraction_and_stopwords():
    terms = extract_terms("Find the screenshot of financial statement in pdf")
    # "the", "of", "in", "screenshot", "pdf" are stopwords/kind words
    assert "financial" in terms
    assert "statement" in terms
    assert "screenshot" not in terms
    assert "pdf" not in terms
    assert "the" not in terms


def test_kind_hints_detection():
    hints = extract_kind_hints("show photo of the mountains and clip of race")
    assert FileKind.IMAGE in hints
    assert FileKind.VIDEO in hints


def test_make_fts_patterns_inflection_wildcard():
    patterns = make_fts_patterns(["invoice", "কুমির", "مدرس"], prefix_last=False)
    assert patterns[0] == '"invoice"'
    assert patterns[1] == '"কুমির"*'  # Inflected Indic gets prefix wildcard
    assert patterns[2] == '"مدرس"*'   # Inflected Arabic gets prefix wildcard


def test_is_lookup_logic():
    assert is_lookup(["ERR404"]) is True
    assert is_lookup(["123456"]) is True
    assert is_lookup(["simple", "terms"]) is False


def test_code_identifier_extraction():
    assert extract_identifier("quantize_row_q8_0_ref") == ["quantize", "row", "q8", "0", "ref"]
    assert extract_identifier("retryWithBackoff()") == ["retrywithbackoff"]
    assert extract_identifier("models.User.find") == ["models", "user", "find"]
    assert extract_identifier("simple") is None
    assert extract_identifier("regular query text") is None


def test_identifier_phrase():
    words = ["quantize", "row", "q8"]
    assert identifier_phrase(words) == '"quantize row q8"'


def test_find_definition_strengths():
    text = (
        "import os\n"
        "\n"
        "def compute_hash(data):\n"
        "    return hashlib.sha256(data).hexdigest()\n"
        "\n"
        "class TokenBucket:\n"
        "    pass\n"
    )
    def_res = find_definition("compute_hash", text, first_line=1)
    assert def_res is not None
    line, strength = def_res
    assert line == 3
    assert strength == 2

    cls_res = find_definition("TokenBucket", text, first_line=10)
    assert cls_res is not None
    assert cls_res[0] == 15
    assert cls_res[1] == 2


def test_find_first_line_with_words():
    text = (
        "# Commentary about server initialization\n"
        "# Another comment line\n"
        "server = create_instance()\n"
        "def start_server():\n"
        "    server.listen()\n"
    )
    # Prefer definition line
    line = find_first_line_with_words(["server"], text, first_line=1)
    assert line == 4  # def start_server() has definer token


def test_prompts_document_formatting():
    assert format_doc_prompt("specs", "content text") == "title: specs | text: content text"
    assert format_doc_prompt("", "content text") == "title: none | text: content text"


def test_ollama_client_resolve_model_name(monkeypatch: pytest.MonkeyPatch):
    client = OllamaClient()

    def mock_get(url: str, timeout: float):
        return httpx.Response(
            200,
            json={"models": [{"name": "hf.co/ggml-org/embeddinggemma-2-GGUF:BF16"}]},
        )

    monkeypatch.setattr(httpx, "get", mock_get)

    # Preferred exact match
    resolved_exact = client.resolve_model_name("hf.co/ggml-org/embeddinggemma-2-GGUF:BF16")
    assert resolved_exact == "hf.co/ggml-org/embeddinggemma-2-GGUF:BF16"

    # Fuzzy match from embeddinggemma-2 to installed tag
    resolved_fuzzy = client.resolve_model_name("embeddinggemma-2")
    assert resolved_fuzzy == "hf.co/ggml-org/embeddinggemma-2-GGUF:BF16"


def test_multi_segment_coverage_preserves_document(tmp_path: Path):
    """Verify terms distributed across different segments of the same file achieve 100% coverage."""
    db = Database(tmp_path / "coverage.sqlite")
    c = Candidate(path="/tmp/large_manual.pdf", kind=FileKind.PDF, size=5000, mtime=1.0, inode=10, device=1)
    fid = db.insert_file(c)

    # Segment 1 (Page 1) contains "diagnostic"
    db.insert_segment(
        file_id=fid,
        kind=SegmentKind.PAGE,
        modality=Modality.TEXT,
        loc=1.0,
        loc_end=1.0,
        excerpt="Page 1 diagnostic information",
        vec=None,
        name="large_manual.pdf",
        body="diagnostic information",
        original="",
    )
    # Segment 2 (Page 2) contains "recovery"
    db.insert_segment(
        file_id=fid,
        kind=SegmentKind.PAGE,
        modality=Modality.TEXT,
        loc=2.0,
        loc_end=2.0,
        excerpt="Page 2 recovery procedures",
        vec=None,
        name="large_manual.pdf",
        body="recovery procedures",
        original="",
    )
    # Segment 3 (Page 3) contains "protocol"
    db.insert_segment(
        file_id=fid,
        kind=SegmentKind.PAGE,
        modality=Modality.TEXT,
        loc=3.0,
        loc_end=3.0,
        excerpt="Page 3 protocol guidelines",
        vec=None,
        name="large_manual.pdf",
        body="protocol guidelines",
        original="",
    )

    searcher = SearchService(db=db)
    # Query with all 3 terms across 3 separate segments
    results = searcher.search("diagnostic recovery protocol")

    assert len(results.ranked) == 1
    hit = results.ranked[0]
    assert hit.path == "/tmp/large_manual.pdf"
    assert hit.score > 0.0


def test_code_identifier_phrase_match_and_definition_jump(tmp_path: Path):
    """Verify searching for identifier jumps to segment and line of definition."""
    code_path = tmp_path / "math_ops.py"
    code_path.write_text(
        "# Math operations module\n"
        "import math\n\n"
        "def quantize_row_q8_0(weights):\n"
        "    return [round(w) for w in weights]\n\n"
        "class Tensor:\n"
        "    pass\n",
        encoding="utf-8",
    )
    code_db = Database(tmp_path / "code_ops.sqlite")
    c = Candidate(path=str(code_path), kind=FileKind.CODE, size=150, mtime=1.0, inode=20, device=1)
    fid = code_db.insert_file(c)

    code_db.insert_segment(
        file_id=fid,
        kind=SegmentKind.LINES,
        modality=Modality.TEXT,
        loc=1.0,
        loc_end=7.0,
        excerpt="def quantize_row_q8_0",
        vec=None,
        name="math_ops.py",
        body="def quantize_row_q8_0 weights return round w for w in weights",
        original=code_path.read_text(encoding="utf-8"),
    )

    searcher = SearchService(db=Database(":memory:"), code_db=code_db)
    results = searcher.search("code: quantize_row_q8_0")

    assert len(results.ranked) == 1
    hit = results.ranked[0]
    assert hit.focus == 4.0  # Line 4 is where quantize_row_q8_0 is defined


def test_asymmetry_embedding_payload_formatting(tmp_path: Path):
    """Verify document/PDF payloads format as title: {file_stem} | text: {text}

    and code payloads format as title: {repo_relative_path} | text: {text}.
    """
    # 1. Document / PDF text embedding format
    doc_path = tmp_path / "research_paper.pdf"
    file_stem = doc_path.stem
    doc_text = "This is the content text of the PDF document."
    doc_payload = format_doc_prompt(file_stem, doc_text)
    assert doc_payload == "title: research_paper | text: This is the content text of the PDF document."

    # 2. Code embedding format using repo-relative path
    repo_dir = tmp_path / "my_project"
    code_file = repo_dir / "src" / "services" / "search.py"
    code_file.parent.mkdir(parents=True, exist_ok=True)
    code_file.write_text("def run_search(): pass")

    repo, repo_rel_path = _code_place(str(code_file), code_folders=[str(repo_dir)])
    assert repo == str(repo_dir)
    assert repo_rel_path == "src/services/search.py"

    code_text = "def run_search(): pass"
    code_payload = format_doc_prompt(repo_rel_path, code_text)
    assert code_payload == "title: src/services/search.py | text: def run_search(): pass"

    # 3. Code repo detection via package.json marker without code_folders configured
    npm_repo = tmp_path / "frontend_app"
    npm_repo.mkdir()
    (npm_repo / "package.json").write_text("{}")
    js_file = npm_repo / "components" / "button.tsx"
    js_file.parent.mkdir(parents=True, exist_ok=True)
    js_file.write_text("export function Button() {}")

    found_repo, rel_path = _code_place(str(js_file))
    assert found_repo == str(npm_repo)
    assert rel_path == "components/button.tsx"
