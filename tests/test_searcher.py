"""Tests for hybrid search service, routing, ranking, and folding."""

from pathlib import Path
import numpy as np
import pytest

from semsearch.core.database import Database
from semsearch.core.ollama import (
    OllamaClient,
    format_code_query_prompt,
    format_query_prompt,
)
from semsearch.core.search_text import is_lookup_query, make_fts_patterns
from semsearch.core.types import (
    Candidate,
    FileKind,
    Modality,
    SearchHit,
    SegmentKind,
)
from semsearch.core.vectors import DIM, float16_to_bytes, l2_normalize
from semsearch.services.searcher import ResultSet, SearchService


@pytest.fixture
def mock_dbs(tmp_path: Path) -> tuple[Database, Database]:
    db = Database(tmp_path / "index.sqlite")
    code_db = Database(tmp_path / "code.sqlite")
    return db, code_db


def test_prompts_formatting():
    assert format_query_prompt("quarterly report") == "task: search result | query: quarterly report"
    assert format_code_query_prompt("binary search") == "task: code retrieval | query: binary search"


def test_is_lookup_query():
    assert is_lookup_query("getUserProfile") is True
    assert is_lookup_query("get_user_profile") is True
    assert is_lookup_query("src/core/types.py") is True
    assert is_lookup_query("models.User") is True
    assert is_lookup_query("std::vector") is True
    assert is_lookup_query("ABC-12345") is True
    assert is_lookup_query("0x7ffcd3") is True
    assert is_lookup_query("error404") is True
    assert is_lookup_query("natural language query") is False
    assert is_lookup_query("simple search text") is False
    assert is_lookup_query("") is False


def test_compute_coverage(mock_dbs):
    db, _ = mock_dbs
    assert db.compute_coverage([], [1]) == {}
    text = "The annual financial report for fiscal year 2026 is published."
    fid = db.insert_file(Candidate(path="/docs/report.txt", kind=FileKind.DOC, size=50, mtime=1.0, inode=1, device=1))
    db.insert_segment(fid, SegmentKind.CHUNK, Modality.TEXT, 0.0, 0.0, text, None, "report.txt", text, "")

    patterns = make_fts_patterns(["financial", "report", "2026"])
    assert db.compute_coverage(patterns, [fid])[fid] == 1.0

    # Partial prefix match on last term
    patterns_prefix = make_fts_patterns(["financial", "repo"], prefix_last=True)
    assert db.compute_coverage(patterns_prefix, [fid])[fid] == 1.0

    # Partial match missing some terms
    patterns_partial = make_fts_patterns(["financial", "budget", "quarterly"])
    assert pytest.approx(db.compute_coverage(patterns_partial, [fid])[fid], 0.01) == 0.33


def test_query_routing_general_vs_code(mock_dbs):
    db, code_db = mock_dbs

    # Insert file in general db
    c1 = Candidate(path="/docs/specs.txt", kind=FileKind.DOC, size=100, mtime=1.0, inode=1, device=1)
    fid1 = db.insert_file(c1)
    db.insert_segment(fid1, SegmentKind.CHUNK, Modality.TEXT, 0, 0, "system specifications", None, "specs.txt", "system specifications", "specs")

    # Insert file in code db
    c2 = Candidate(path="/src/engine.py", kind=FileKind.CODE, size=200, mtime=1.0, inode=2, device=2)
    fid2 = code_db.insert_file(c2)
    code_db.insert_segment(fid2, SegmentKind.LINES, Modality.TEXT, 1, 10, "class SearchEngine:\n    pass", None, "engine.py", "class SearchEngine pass", "engine")

    searcher = SearchService(db=db, code_db=code_db)

    # General search routes to db
    res_gen = searcher.search("specifications")
    assert len(res_gen.ranked) == 1
    assert res_gen.ranked[0].path == "/docs/specs.txt"

    # Code search routes to code_db
    res_code = searcher.search("code: SearchEngine")
    assert len(res_code.ranked) == 1
    assert res_code.ranked[0].path == "/src/engine.py"

    # Code search when code_db is None returns empty
    searcher_no_code = SearchService(db=db, code_db=None)
    assert len(searcher_no_code.search("code: SearchEngine")) == 0


def test_quality_gating_and_ranking(mock_dbs):
    db, _ = mock_dbs

    # High coverage keyword match (coverage >= 0.5)
    c1 = Candidate(path="/docs/invoice.txt", kind=FileKind.DOC, size=100, mtime=1.0, inode=1, device=1)
    fid1 = db.insert_file(c1)
    db.insert_segment(fid1, SegmentKind.CHUNK, Modality.TEXT, 0, 0, "client invoice billing", None, "invoice.txt", "client invoice billing payment", "")

    # Low coverage keyword match (coverage < 0.5) and no vector
    c2 = Candidate(path="/docs/random.txt", kind=FileKind.DOC, size=100, mtime=1.0, inode=2, device=2)
    fid2 = db.insert_file(c2)
    db.insert_segment(fid2, SegmentKind.CHUNK, Modality.TEXT, 0, 0, "unrelated notes", None, "random.txt", "only client mentioned here", "")

    searcher = SearchService(db=db)
    results = searcher.search("client invoice billing payment")

    # c1 matches 3/4 terms (coverage 0.75 >= 0.5) -> passes gate
    # c2 matches 1/4 terms (coverage 0.25 < 0.5) -> discarded by gate
    assert len(results.ranked) == 1
    assert results.ranked[0].path == "/docs/invoice.txt"


def test_vector_search_with_mock_ollama(mock_dbs, monkeypatch):
    db, _ = mock_dbs
    rng = np.random.default_rng(42)

    vec_high = l2_normalize(rng.standard_normal(DIM).astype(np.float32))
    vec_low = -vec_high

    c1 = Candidate(path="/images/sunset.jpg", kind=FileKind.IMAGE, size=500, mtime=1.0, inode=1, device=1)
    fid1 = db.insert_file(c1)
    db.insert_segment(
        fid1, SegmentKind.IMAGE, Modality.IMAGE, 0, 0, "sunset over the beach",
        float16_to_bytes(vec_high), "sunset.jpg", "", ""
    )

    c2 = Candidate(path="/images/mountains.jpg", kind=FileKind.IMAGE, size=500, mtime=1.0, inode=2, device=2)
    fid2 = db.insert_file(c2)
    db.insert_segment(
        fid2, SegmentKind.IMAGE, Modality.IMAGE, 0, 0, "snowy peaks",
        float16_to_bytes(vec_low), "mountains.jpg", "", ""
    )

    class MockOllama(OllamaClient):
        def embed(self, model: str, inputs: list[str | dict[str, str]], truncate: bool = True) -> list[list[float]]:
            return [vec_high.tolist()]

    searcher = SearchService(db=db, ollama=MockOllama())
    results = searcher.search("beach sunset")

    assert len(results.ranked) >= 1
    assert results.ranked[0].path == "/images/sunset.jpg"
    assert results.ranked[0].z > 0.0


def test_weaker_matches_folding(mock_dbs):
    db, _ = mock_dbs

    c1 = Candidate(path="/a.txt", kind=FileKind.DOC, size=10, mtime=1.0, inode=1, device=1)
    fid1 = db.insert_file(c1)
    db.insert_segment(fid1, SegmentKind.CHUNK, Modality.TEXT, 0, 0, "alpha beta gamma delta", None, "a.txt", "alpha beta gamma delta", "")

    c2 = Candidate(path="/b.txt", kind=FileKind.DOC, size=10, mtime=1.0, inode=2, device=2)
    fid2 = db.insert_file(c2)
    db.insert_segment(fid2, SegmentKind.CHUNK, Modality.TEXT, 0, 0, "alpha beta", None, "b.txt", "alpha beta", "")

    searcher = SearchService(db=db)
    # Query matching all terms in c1 and half in c2
    results = searcher.search("alpha beta gamma delta")

    assert len(results.ranked) >= 1
    assert results.ranked[0].path == "/a.txt"
    # c2 score is less than 0.5 of c1 score because coverage 0.5^1.5 = 0.353
    # topScore has coverage 1.0^1.5 = 1.0 (with same weights) -> 0.353 < 0.5 * 1.0
    assert any(h.path == "/b.txt" for h in results.weaker)


def test_code_definition_line_jump(tmp_path: Path):
    code_path = tmp_path / "parser.py"
    code_path.write_text(
        "import sys\n\ndef parse_tokens(stream):\n    return []\n",
        encoding="utf-8",
    )
    code_db = Database(tmp_path / "code.sqlite")
    c = Candidate(path=str(code_path), kind=FileKind.CODE, size=100, mtime=1.0, inode=1, device=1)
    fid = code_db.insert_file(c)
    code_db.insert_segment(fid, SegmentKind.LINES, Modality.TEXT, 1, 4, "def parse_tokens", None, "parser.py", "parse tokens", "")

    searcher = SearchService(db=Database(":memory:"), code_db=code_db)
    results = searcher.search("code: parse_tokens")

    assert len(results.ranked) == 1
    hit = results.ranked[0]
    assert hit.focus == 3.0  # Line 3 is where `def parse_tokens` is defined!


def test_fts_keyword_rank_positive_and_matches_words(tmp_path: Path):
    """Verify FTS matches yield positive keyword_rank and hit.reason is 'Matches words'."""
    code_path = tmp_path / "service.py"
    code_path.write_text("def handle_request(): pass\n", encoding="utf-8")
    db = Database(tmp_path / "test_kw.sqlite")
    c = Candidate(path=str(code_path), kind=FileKind.CODE, size=50, mtime=1.0, inode=1, device=1)
    fid = db.insert_file(c)
    db.insert_segment(
        file_id=fid,
        kind=SegmentKind.LINES,
        modality=Modality.TEXT,
        loc=1.0,
        loc_end=1.0,
        excerpt="def handle_request(): pass",
        vec=None,
        name="service.py",
        body="def handle_request(): pass",
        original="",
    )

    searcher = SearchService(db=db)
    results = searcher.search("handle_request")

    assert len(results.ranked) == 1
    hit = results.ranked[0]
    assert hit.keyword_rank > 0
    assert hit.reason == "Matches words"


def test_filename_match_outranks_incidental_body_mention(mock_dbs):
    """Verify a file matching query terms in its filename outranks a file mentioning them only in body."""
    db, _ = mock_dbs

    c_body = Candidate(
        path="/docs/informacion_fiscal_2025.pdf",
        kind=FileKind.PDF,
        size=1000,
        mtime=10.0,
        inode=1,
        device=1,
    )
    fid_body = db.insert_file(c_body)
    db.insert_segment(
        fid_body,
        SegmentKind.PAGE,
        Modality.TEXT,
        1.0,
        1.0,
        "Declaración de impuestos de Juan Ezquerro",
        None,
        "",
        "Declaración de impuestos de Juan Ezquerro",
        "",
    )

    c_name = Candidate(
        path="/docs/Autorización Juan Ezquerro.pdf",
        kind=FileKind.PDF,
        size=500,
        mtime=5.0,
        inode=2,
        device=1,
    )
    fid_name = db.insert_file(c_name)
    db.insert_segment(
        fid_name,
        SegmentKind.PAGE,
        Modality.TEXT,
        1.0,
        1.0,
        "Documento firmado",
        None,
        "",
        "Documento firmado",
        "",
    )

    searcher = SearchService(db=db)
    res = searcher.search("juan ezquerro")
    assert len(res.ranked) >= 1
    assert res.ranked[0].path == "/docs/Autorización Juan Ezquerro.pdf"


def test_pure_kind_hint_query_returns_matching_files(mock_dbs):
    """Verify queries consisting only of kind hint words (e.g. 'captura de pantalla') return matching files."""
    db, _ = mock_dbs
    c_shot = Candidate(
        path="/pics/Captura de pantalla_20260927.png",
        kind=FileKind.SCREENSHOT,
        size=200,
        mtime=20.0,
        inode=1,
        device=1,
    )
    fid_shot = db.insert_file(c_shot)
    db.insert_segment(fid_shot, SegmentKind.IMAGE, Modality.IMAGE, 0.0, 0.0, "screenshot", None, "", "", "")

    c_doc = Candidate(
        path="/docs/manual_de_usuario.pdf",
        kind=FileKind.PDF,
        size=300,
        mtime=30.0,
        inode=2,
        device=1,
    )
    fid_doc = db.insert_file(c_doc)
    db.insert_segment(
        fid_doc,
        SegmentKind.PAGE,
        Modality.TEXT,
        1.0,
        1.0,
        "Manual de instrucciones en español",
        None,
        "",
        "Manual de instrucciones de uso",
        "",
    )

    searcher = SearchService(db=db)
    res = searcher.search("captura de pantalla")
    assert len(res.ranked) == 1
    assert res.ranked[0].path == "/pics/Captura de pantalla_20260927.png"


def test_code_definition_bonus_outranks_caller(tmp_path: Path):
    """Verify code file defining a class/function ranks above files that merely import or call it."""
    code_db = Database(tmp_path / "code.sqlite")

    caller_path = tmp_path / "window.py"
    caller_path.write_text("from searcher import SearchService\nsvc = SearchService()\n", encoding="utf-8")
    fid_caller = code_db.insert_file(
        Candidate(str(caller_path), FileKind.CODE, 80, 20.0, 1, 1)
    )
    code_db.insert_segment(
        fid_caller,
        SegmentKind.LINES,
        Modality.TEXT,
        1.0,
        2.0,
        "from searcher import SearchService",
        None,
        "window.py",
        caller_path.read_text(encoding="utf-8"),
        caller_path.read_text(encoding="utf-8"),
    )

    def_path = tmp_path / "searcher.py"
    def_path.write_text("# Search module\nclass SearchService:\n    pass\n", encoding="utf-8")
    fid_def = code_db.insert_file(
        Candidate(str(def_path), FileKind.CODE, 80, 10.0, 2, 1)
    )
    code_db.insert_segment(
        fid_def,
        SegmentKind.LINES,
        Modality.TEXT,
        1.0,
        3.0,
        "class SearchService:",
        None,
        "searcher.py",
        def_path.read_text(encoding="utf-8"),
        def_path.read_text(encoding="utf-8"),
    )

    searcher = SearchService(db=Database(":memory:"), code_db=code_db)
    res = searcher.search("code: SearchService")
    assert len(res.ranked) >= 1
    assert res.ranked[0].path == str(def_path)
    assert res.ranked[0].focus == 2.0


def test_semantic_z_gate_prunes_weak_noise_and_retains_strong_match(mock_dbs):
    """Verify semantic candidates with z < 2.2 and no lexical match are pruned while z >= 2.2 passes."""
    db, _ = mock_dbs
    rng = np.random.default_rng(7)
    base_vec = l2_normalize(rng.standard_normal(DIM).astype(np.float32))
    ortho_vec = l2_normalize(rng.standard_normal(DIM).astype(np.float32))
    ortho_vec = l2_normalize(ortho_vec - np.dot(ortho_vec, base_vec) * base_vec)

    def make_vec_with_cos(c: float) -> bytes:
        v = c * base_vec + np.sqrt(max(0.0, 1.0 - c * c)) * ortho_vec
        return float16_to_bytes(l2_normalize(v.astype(np.float32)))

    # 17 background files with cos ~ 0.58
    for i in range(17):
        fid = db.insert_file(Candidate(f"/docs/bg_{i}.txt", FileKind.DOC, 100, 1.0, i + 1, 1))
        db.insert_segment(
            fid, SegmentKind.CHUNK, Modality.TEXT, 0.0, 0.0, "unrelated text",
            make_vec_with_cos(0.58), f"bg_{i}.txt", "unrelated text", ""
        )

    # Weak semantic candidate (cos ~ 0.66 -> z < 2.2)
    fid_weak = db.insert_file(Candidate("/docs/Bienvenido.md", FileKind.DOC, 200, 2.0, 100, 1))
    db.insert_segment(
        fid_weak, SegmentKind.CHUNK, Modality.TEXT, 0.0, 0.0, "Esta es su nueva boveda",
        make_vec_with_cos(0.66), "Bienvenido.md", "Esta es su nueva boveda", ""
    )

    class QueryMockOllama(OllamaClient):
        def embed(self, model: str, inputs: list[str | dict[str, str]], truncate: bool = True) -> list[list[float]]:
            return [base_vec.tolist()]

    searcher = SearchService(db=db, ollama=QueryMockOllama())
    # With only background + weak candidate (z < 2.2), no false positives are returned
    res_noise = searcher.search("agujero")
    assert len(res_noise.ranked) == 0

    # Add two genuine semantic matches (two black hole images: cos ~ 0.743 and cos ~ 0.689)
    fid_bh1 = db.insert_file(Candidate("/pics/descarga.png", FileKind.IMAGE, 500, 3.0, 101, 1))
    db.insert_segment(
        fid_bh1, SegmentKind.IMAGE, Modality.IMAGE, 0.0, 0.0, "",
        make_vec_with_cos(0.743), "descarga.png", "", ""
    )
    fid_bh2 = db.insert_file(Candidate("/pics/captura_black_hole.png", FileKind.SCREENSHOT, 600, 4.0, 102, 1))
    db.insert_segment(
        fid_bh2, SegmentKind.IMAGE, Modality.IMAGE, 0.0, 0.0, "",
        make_vec_with_cos(0.689), "captura_black_hole.png", "", ""
    )

    res_strong = searcher.search("agujero negro")
    assert len(res_strong.ranked) == 2
    assert [h.path for h in res_strong.ranked] == ["/pics/descarga.png", "/pics/captura_black_hole.png"]
    assert all(h.z >= 2.2 for h in res_strong.ranked)
    assert all(h.reason == "Matches meaning" for h in res_strong.ranked)

