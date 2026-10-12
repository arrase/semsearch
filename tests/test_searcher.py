"""Tests for hybrid search service, routing, ranking, and folding."""

from pathlib import Path
import numpy as np
import pytest

from semsearch.core.database import Database
from semsearch.core.ollama import OllamaClient
from semsearch.core.types import (
    Candidate,
    FileKind,
    Modality,
    ResultRow,
    SearchHit,
    SegmentKind,
)
from semsearch.core.vectors import DIM, float16_to_bytes, l2_normalize
from semsearch.services.searcher import Prompts, ResultSet, SearchService


@pytest.fixture
def mock_dbs(tmp_path: Path) -> tuple[Database, Database]:
    db = Database(tmp_path / "index.sqlite")
    code_db = Database(tmp_path / "code.sqlite")
    return db, code_db


def test_prompts_formatting():
    assert Prompts.query("quarterly report") == "task: search result | query: quarterly report"
    assert Prompts.code_query("binary search") == "task: code retrieval | query: binary search"


def test_is_lookup_query():
    searcher = SearchService(db=Database(":memory:"))
    assert searcher.is_lookup_query("getUserProfile") is True
    assert searcher.is_lookup_query("get_user_profile") is True
    assert searcher.is_lookup_query("src/core/types.py") is True
    assert searcher.is_lookup_query("models.User") is True
    assert searcher.is_lookup_query("std::vector") is True
    assert searcher.is_lookup_query("ABC-12345") is True
    assert searcher.is_lookup_query("0x7ffcd3") is True
    assert searcher.is_lookup_query("error404") is True
    assert searcher.is_lookup_query("natural language query") is False
    assert searcher.is_lookup_query("simple search text") is False
    assert searcher.is_lookup_query("") is False


def test_compute_coverage():
    searcher = SearchService(db=Database(":memory:"))
    assert searcher.compute_coverage([], "some text") == 0.0
    terms = ["financial", "report", "2026"]
    text = "The annual financial report for fiscal year 2026 is published."
    assert searcher.compute_coverage(terms, text) == 1.0

    # Partial prefix match on last term
    terms_prefix = ["financial", "repo"]
    assert searcher.compute_coverage(terms_prefix, text) == 1.0

    # Partial match missing some terms
    terms_partial = ["financial", "budget", "quarterly"]
    assert pytest.approx(searcher.compute_coverage(terms_partial, text), 0.01) == 0.33


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


def test_format_results_view_models(mock_dbs):
    searcher = SearchService(db=Database(":memory:"))
    hit1 = SearchHit(
        path="/src/main.py",
        kind=FileKind.CODE,
        score=5.0,
        z=2.5,
        keyword_rank=-1,
        segment=SegmentKind.LINES,
        loc=10.0,
        loc_end=20.0,
        excerpt="def start_server():",
        focus=10.0,
    )
    hit2 = SearchHit(
        path="/src/worker.py",
        kind=FileKind.CODE,
        score=1.0,
        z=0.0,
        keyword_rank=1,
        segment=SegmentKind.LINES,
        loc=5.0,
        excerpt="worker thread",
    )
    res_set = ResultSet(ranked=[hit1], weaker=[hit2])
    primary, weaker = searcher.format_results(res_set, query="start_server")

    assert len(primary) == 1
    assert len(weaker) == 1
    assert primary[0].title == "main.py"
    assert primary[0].code_line == 10
    assert primary[0].matched_meaning is True
    assert weaker[0].title == "worker.py"


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
