"""Tests for hybrid search service, routing, ranking, and folding."""

import time
from pathlib import Path

import numpy as np
import pytest

from semsearch.core.database import Database
from semsearch.core.ollama import (
    OllamaClient,
    format_code_query_prompt,
    format_query_prompt,
)
from semsearch.core.search_text import extract_snippet, is_lookup_query, make_fts_patterns
from semsearch.core.types import (
    Candidate,
    FileKind,
    Modality,
    SegmentKind,
)
from semsearch.core.vectors import DIM, float16_to_bytes, l2_normalize
from semsearch.services.searcher import SearchService


@pytest.fixture
def mock_dbs(tmp_path: Path) -> tuple[Database, Database]:
    db = Database(tmp_path / "index.sqlite")
    code_db = Database(tmp_path / "code.sqlite")
    return db, code_db


def test_prompts_formatting() -> None:
    assert format_query_prompt("quarterly report") == "task: search result | query: quarterly report"
    assert format_code_query_prompt("binary search") == "task: code retrieval | query: binary search"


def test_is_lookup_query() -> None:
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


def test_compute_coverage(mock_dbs: tuple[Database, Database]) -> None:
    db, _ = mock_dbs
    assert db.compute_coverage([], [1]) == {}
    text = "The annual financial report for fiscal year 2026 is published."
    fid = db.insert_file(Candidate(path="/docs/report.txt", kind=FileKind.DOC, size=50, mtime=1.0, inode=1, device=1))
    db.insert_segment(
        fid,
        SegmentKind.CHUNK,
        Modality.TEXT,
        0.0,
        0.0,
        excerpt=text,
        vec=None,
        name="report.txt",
        body=text,
        original="",
    )

    patterns = make_fts_patterns(["financial", "report", "2026"])
    assert db.compute_coverage(patterns, [fid])[fid] == 1.0

    # Partial prefix match on last term
    patterns_prefix = make_fts_patterns(["financial", "repo"], prefix_last=True)
    assert db.compute_coverage(patterns_prefix, [fid])[fid] == 1.0

    # Partial match missing some terms
    patterns_partial = make_fts_patterns(["financial", "budget", "quarterly"])
    assert pytest.approx(db.compute_coverage(patterns_partial, [fid])[fid], 0.01) == 0.33


def test_query_routing_general_vs_code(mock_dbs: tuple[Database, Database]) -> None:
    db, code_db = mock_dbs

    # Insert file in general db
    c1 = Candidate(path="/docs/specs.txt", kind=FileKind.DOC, size=100, mtime=1.0, inode=1, device=1)
    fid1 = db.insert_file(c1)
    db.insert_segment(
        fid1,
        SegmentKind.CHUNK,
        Modality.TEXT,
        0,
        0,
        excerpt="system specifications",
        vec=None,
        name="specs.txt",
        body="system specifications",
        original="specs",
    )

    # Insert file in code db
    c2 = Candidate(path="/src/engine.py", kind=FileKind.CODE, size=200, mtime=1.0, inode=2, device=2)
    fid2 = code_db.insert_file(c2)
    code_db.insert_segment(
        fid2,
        SegmentKind.LINES,
        Modality.TEXT,
        1,
        10,
        excerpt="class SearchEngine:\n    pass",
        vec=None,
        name="engine.py",
        body="class SearchEngine pass",
        original="engine",
    )

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


def test_quality_gating_and_ranking(mock_dbs: tuple[Database, Database]) -> None:
    db, _ = mock_dbs

    # High coverage keyword match (coverage >= 0.5)
    c1 = Candidate(path="/docs/invoice.txt", kind=FileKind.DOC, size=100, mtime=1.0, inode=1, device=1)
    fid1 = db.insert_file(c1)
    db.insert_segment(
        fid1,
        SegmentKind.CHUNK,
        Modality.TEXT,
        0,
        0,
        excerpt="client invoice billing",
        vec=None,
        name="invoice.txt",
        body="client invoice billing payment",
        original="",
    )

    # Low coverage keyword match (coverage < 0.5) and no vector
    c2 = Candidate(path="/docs/random.txt", kind=FileKind.DOC, size=100, mtime=1.0, inode=2, device=2)
    fid2 = db.insert_file(c2)
    db.insert_segment(
        fid2,
        SegmentKind.CHUNK,
        Modality.TEXT,
        0,
        0,
        excerpt="unrelated notes",
        vec=None,
        name="random.txt",
        body="only client mentioned here",
        original="",
    )

    searcher = SearchService(db=db)
    results = searcher.search("client invoice billing payment")

    # c1 matches 3/4 terms (coverage 0.75 >= 0.5) -> passes gate
    # c2 matches 1/4 terms (coverage 0.25 < 0.5) -> discarded by gate
    assert len(results.ranked) == 1
    assert results.ranked[0].path == "/docs/invoice.txt"


def test_vector_search_with_mock_ollama(mock_dbs: tuple[Database, Database]) -> None:
    db, _ = mock_dbs
    rng = np.random.default_rng(42)

    vec_high = l2_normalize(rng.standard_normal(DIM).astype(np.float32))
    vec_low = -vec_high

    c1 = Candidate(path="/images/sunset.jpg", kind=FileKind.IMAGE, size=500, mtime=1.0, inode=1, device=1)
    fid1 = db.insert_file(c1)
    db.insert_segment(
        fid1,
        SegmentKind.IMAGE,
        Modality.IMAGE,
        0,
        0,
        excerpt="sunset over the beach",
        vec=float16_to_bytes(vec_high),
        name="sunset.jpg",
        body="",
        original="",
    )

    c2 = Candidate(path="/images/mountains.jpg", kind=FileKind.IMAGE, size=500, mtime=1.0, inode=2, device=2)
    fid2 = db.insert_file(c2)
    db.insert_segment(
        fid2,
        SegmentKind.IMAGE,
        Modality.IMAGE,
        0,
        0,
        excerpt="snowy peaks",
        vec=float16_to_bytes(vec_low),
        name="mountains.jpg",
        body="",
        original="",
    )

    class MockOllama(OllamaClient):
        def embed(
            self,
            _model: str,
            _inputs: list[str | dict[str, str]],
            *,
            truncate: bool = True,
            num_ctx: int | None = None,
            keep_alive: int | str | None = None,
        ) -> list[list[float]]:
            del truncate, num_ctx, keep_alive
            return [vec_high.tolist()]

    searcher = SearchService(db=db, ollama=MockOllama())
    results = searcher.search("beach sunset")

    assert len(results.ranked) >= 1
    assert results.ranked[0].path == "/images/sunset.jpg"
    assert results.ranked[0].z > 0.0


def test_weaker_matches_folding(mock_dbs: tuple[Database, Database]) -> None:
    db, _ = mock_dbs

    c1 = Candidate(path="/a.txt", kind=FileKind.DOC, size=10, mtime=1.0, inode=1, device=1)
    fid1 = db.insert_file(c1)
    db.insert_segment(
        fid1,
        SegmentKind.CHUNK,
        Modality.TEXT,
        0,
        0,
        excerpt="alpha beta gamma delta",
        vec=None,
        name="a.txt",
        body="alpha beta gamma delta",
        original="",
    )

    c2 = Candidate(path="/b.txt", kind=FileKind.DOC, size=10, mtime=1.0, inode=2, device=2)
    fid2 = db.insert_file(c2)
    db.insert_segment(
        fid2,
        SegmentKind.CHUNK,
        Modality.TEXT,
        0,
        0,
        excerpt="alpha beta",
        vec=None,
        name="b.txt",
        body="alpha beta",
        original="",
    )

    searcher = SearchService(db=db)
    # Query matching all terms in c1 and half in c2
    results = searcher.search("alpha beta gamma delta")

    assert len(results.ranked) >= 1
    assert results.ranked[0].path == "/a.txt"
    # c2 score is less than 0.5 of c1 score because coverage 0.5^1.5 = 0.353
    # topScore has coverage 1.0^1.5 = 1.0 (with same weights) -> 0.353 < 0.5 * 1.0
    assert any(h.path == "/b.txt" for h in results.weaker)


def test_code_definition_line_jump(tmp_path: Path) -> None:
    code_path = tmp_path / "parser.py"
    code_path.write_text(
        "import sys\n\ndef parse_tokens(stream):\n    return []\n",
        encoding="utf-8",
    )
    code_db = Database(tmp_path / "code.sqlite")
    c = Candidate(path=str(code_path), kind=FileKind.CODE, size=100, mtime=1.0, inode=1, device=1)
    fid = code_db.insert_file(c)
    code_db.insert_segment(
        fid,
        SegmentKind.LINES,
        Modality.TEXT,
        1,
        4,
        excerpt="def parse_tokens",
        vec=None,
        name="parser.py",
        body="parse tokens",
        original="",
    )

    searcher = SearchService(db=Database(":memory:"), code_db=code_db)
    results = searcher.search("code: parse_tokens")

    assert len(results.ranked) == 1
    hit = results.ranked[0]
    assert hit.focus == 3.0  # Line 3 is where `def parse_tokens` is defined!


def test_fts_keyword_rank_positive_and_matches_words(tmp_path: Path) -> None:
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


def test_filename_match_outranks_incidental_body_mention(mock_dbs: tuple[Database, Database]) -> None:
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
        excerpt="Declaración de impuestos de Juan Ezquerro",
        vec=None,
        name="",
        body="Declaración de impuestos de Juan Ezquerro",
        original="",
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
        excerpt="Documento firmado",
        vec=None,
        name="",
        body="Documento firmado",
        original="",
    )

    searcher = SearchService(db=db)
    res = searcher.search("juan ezquerro")
    assert len(res.ranked) >= 1
    assert res.ranked[0].path == "/docs/Autorización Juan Ezquerro.pdf"


def test_pure_kind_hint_query_returns_matching_files(mock_dbs: tuple[Database, Database]) -> None:
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
    db.insert_segment(
        fid_shot,
        SegmentKind.IMAGE,
        Modality.IMAGE,
        0.0,
        0.0,
        excerpt="screenshot",
        vec=None,
        name="",
        body="",
        original="",
    )

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
        excerpt="Manual de instrucciones en español",
        vec=None,
        name="",
        body="Manual de instrucciones de uso",
        original="",
    )

    searcher = SearchService(db=db)
    res = searcher.search("captura de pantalla")
    assert len(res.ranked) == 1
    assert res.ranked[0].path == "/pics/Captura de pantalla_20260927.png"


def test_code_definition_bonus_outranks_caller(tmp_path: Path) -> None:
    """Verify code file defining a class/function ranks above files that merely import or call it."""
    code_db = Database(tmp_path / "code.sqlite")

    caller_path = tmp_path / "window.py"
    caller_path.write_text("from searcher import SearchService\nsvc = SearchService()\n", encoding="utf-8")
    fid_caller = code_db.insert_file(Candidate(str(caller_path), FileKind.CODE, 80, 20.0, 1, 1))
    code_db.insert_segment(
        fid_caller,
        SegmentKind.LINES,
        Modality.TEXT,
        1.0,
        2.0,
        excerpt="from searcher import SearchService",
        vec=None,
        name="window.py",
        body=caller_path.read_text(encoding="utf-8"),
        original=caller_path.read_text(encoding="utf-8"),
    )

    def_path = tmp_path / "searcher.py"
    def_path.write_text("# Search module\nclass SearchService:\n    pass\n", encoding="utf-8")
    fid_def = code_db.insert_file(Candidate(str(def_path), FileKind.CODE, 80, 10.0, 2, 1))
    code_db.insert_segment(
        fid_def,
        SegmentKind.LINES,
        Modality.TEXT,
        1.0,
        3.0,
        excerpt="class SearchService:",
        vec=None,
        name="searcher.py",
        body=def_path.read_text(encoding="utf-8"),
        original=def_path.read_text(encoding="utf-8"),
    )

    searcher = SearchService(db=Database(":memory:"), code_db=code_db)
    res = searcher.search("code: SearchService")
    assert len(res.ranked) >= 1
    assert res.ranked[0].path == str(def_path)
    assert res.ranked[0].focus == 2.0


def test_semantic_z_gate_prunes_weak_noise_and_retains_strong_match(mock_dbs: tuple[Database, Database]) -> None:
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
            fid,
            SegmentKind.CHUNK,
            Modality.TEXT,
            0.0,
            0.0,
            excerpt="unrelated text",
            vec=make_vec_with_cos(0.58),
            name=f"bg_{i}.txt",
            body="unrelated text",
            original="",
        )

    # Weak semantic candidate (cos ~ 0.66 -> z < 2.2)
    fid_weak = db.insert_file(Candidate("/docs/Bienvenido.md", FileKind.DOC, 200, 2.0, 100, 1))
    db.insert_segment(
        fid_weak,
        SegmentKind.CHUNK,
        Modality.TEXT,
        0.0,
        0.0,
        excerpt="Esta es su nueva boveda",
        vec=make_vec_with_cos(0.66),
        name="Bienvenido.md",
        body="Esta es su nueva boveda",
        original="",
    )

    class QueryMockOllama(OllamaClient):
        def embed(
            self,
            _model: str,
            _inputs: list[str | dict[str, str]],
            *,
            truncate: bool = True,
            num_ctx: int | None = None,
            keep_alive: int | str | None = None,
        ) -> list[list[float]]:
            del truncate, num_ctx, keep_alive
            return [base_vec.tolist()]

    searcher = SearchService(db=db, ollama=QueryMockOllama())
    # With only background + weak candidate (z < 2.2), no false positives are returned
    res_noise = searcher.search("agujero")
    assert len(res_noise.ranked) == 0

    # Add two genuine semantic matches (two black hole images: cos ~ 0.743 and cos ~ 0.689)
    fid_bh1 = db.insert_file(Candidate("/pics/descarga.png", FileKind.IMAGE, 500, 3.0, 101, 1))
    db.insert_segment(
        fid_bh1,
        SegmentKind.IMAGE,
        Modality.IMAGE,
        0.0,
        0.0,
        excerpt="",
        vec=make_vec_with_cos(0.743),
        name="descarga.png",
        body="",
        original="",
    )
    fid_bh2 = db.insert_file(Candidate("/pics/captura_black_hole.png", FileKind.SCREENSHOT, 600, 4.0, 102, 1))
    db.insert_segment(
        fid_bh2,
        SegmentKind.IMAGE,
        Modality.IMAGE,
        0.0,
        0.0,
        excerpt="",
        vec=make_vec_with_cos(0.689),
        name="captura_black_hole.png",
        body="",
        original="",
    )

    res_strong = searcher.search("agujero negro")
    assert len(res_strong.ranked) == 2
    assert [h.path for h in res_strong.ranked] == ["/pics/descarga.png", "/pics/captura_black_hole.png"]
    assert all(h.z >= 2.2 for h in res_strong.ranked)
    assert all(h.reason == "Matches meaning" for h in res_strong.ranked)


def test_bm25_outranks_dispersed_long_document(mock_dbs: tuple[Database, Database]) -> None:
    """Verify BM25 bonus ranks a dense, focused match above a long document with dispersed terms."""
    db, _ = mock_dbs

    # Long document with dispersed occurrences and newer mtime (would win tie without BM25)
    filler = " ".join(f"filler{i}" for i in range(250))
    sparse_body = f"kubernetes {filler} ingress {filler} timeout"
    fid_sparse = db.insert_file(Candidate("/docs/encyclopedia.txt", FileKind.DOC, 5000, 200.0, 1, 1))
    db.insert_segment(
        fid_sparse,
        SegmentKind.CHUNK,
        Modality.TEXT,
        excerpt=sparse_body[:80],
        body=sparse_body,
        original=sparse_body,
    )

    # Concise document with high term density and older mtime
    dense_body = "kubernetes ingress timeout kubernetes ingress timeout guide"
    fid_dense = db.insert_file(Candidate("/docs/runbook.txt", FileKind.DOC, 200, 100.0, 2, 1))
    db.insert_segment(
        fid_dense,
        SegmentKind.CHUNK,
        Modality.TEXT,
        excerpt=dense_body,
        body=dense_body,
        original=dense_body,
    )

    searcher = SearchService(db=db)
    res = searcher.search("kubernetes ingress timeout")
    assert len(res.ranked) == 2
    assert res.ranked[0].path == "/docs/runbook.txt"
    assert res.ranked[0].score > res.ranked[1].score


def test_inline_filters_kind_ext_path_and_after(mock_dbs: tuple[Database, Database]) -> None:
    """Verify inline kind:, ext:, path:, and after: filters constrain hybrid search results."""
    db, _ = mock_dbs
    now = time.time()

    fid_pdf = db.insert_file(Candidate("/work/finance/q4_report.pdf", FileKind.PDF, 1000, now - 3600, 1, 1))
    db.insert_segment(
        fid_pdf,
        SegmentKind.PAGE,
        Modality.TEXT,
        loc=1.0,
        excerpt="quarterly budget forecast",
        body="quarterly budget forecast",
        original="quarterly budget forecast",
    )

    fid_md = db.insert_file(Candidate("/work/finance/notes.md", FileKind.DOC, 300, now - 3600, 2, 1))
    db.insert_segment(
        fid_md,
        SegmentKind.CHUNK,
        Modality.TEXT,
        excerpt="quarterly budget forecast notes",
        body="quarterly budget forecast notes",
        original="quarterly budget forecast notes",
    )

    fid_old = db.insert_file(Candidate("/personal/archive/old_budget.pdf", FileKind.PDF, 1000, now - 60 * 86400, 3, 1))
    db.insert_segment(
        fid_old,
        SegmentKind.PAGE,
        Modality.TEXT,
        loc=1.0,
        excerpt="quarterly budget forecast 2023",
        body="quarterly budget forecast 2023",
        original="quarterly budget forecast 2023",
    )

    searcher = SearchService(db=db)

    res_kind = searcher.search("kind:pdf quarterly budget")
    assert {h.path for h in res_kind.ranked} == {"/work/finance/q4_report.pdf", "/personal/archive/old_budget.pdf"}

    res_ext = searcher.search("ext:md quarterly budget")
    assert [h.path for h in res_ext.ranked] == ["/work/finance/notes.md"]

    res_path = searcher.search("path:FINANCE quarterly budget")
    assert {h.path for h in res_path.ranked} == {"/work/finance/q4_report.pdf", "/work/finance/notes.md"}

    res_after = searcher.search("after:7d quarterly budget")
    assert {h.path for h in res_after.ranked} == {"/work/finance/q4_report.pdf", "/work/finance/notes.md"}

    # Combining explicit kinds argument with inline kind: intersects both constraints
    res_combined = searcher.search("kind:pdf quarterly budget", kinds={FileKind.DOC})
    assert len(res_combined.ranked) == 0


def test_pure_filter_query_skips_ollama(mock_dbs: tuple[Database, Database]) -> None:
    """Verify pure filter queries (kind:pdf, after:7d) return matching files without calling Ollama."""
    db, _ = mock_dbs
    now = time.time()

    db.insert_file(Candidate("/docs/recent.pdf", FileKind.PDF, 100, now - 600, 1, 1))
    db.insert_file(Candidate("/docs/old.pdf", FileKind.PDF, 100, now - 30 * 86400, 2, 1))
    db.insert_file(Candidate("/docs/recent.md", FileKind.DOC, 100, now - 300, 3, 1))

    class CountingOllama(OllamaClient):
        def __init__(self) -> None:
            super().__init__()
            self.embed_calls = 0

        def embed(
            self,
            _model: str,
            inputs: list[str | dict[str, str]],
            *,
            truncate: bool = True,
            num_ctx: int | None = None,
            keep_alive: int | str | None = None,
        ) -> list[list[float]]:
            del inputs, truncate, num_ctx, keep_alive
            self.embed_calls += 1
            return []

    with CountingOllama() as ollama:
        searcher = SearchService(db=db, ollama=ollama)

        res_pdf = searcher.search("kind:pdf")
        assert [h.path for h in res_pdf.ranked] == ["/docs/recent.pdf", "/docs/old.pdf"]
        assert ollama.embed_calls == 0

        res_recent = searcher.search("after:7d")
        assert [h.path for h in res_recent.ranked] == ["/docs/recent.md", "/docs/recent.pdf"]
        assert ollama.embed_calls == 0


def test_lexical_search_filters_and_disabled_code_db(mock_dbs: tuple[Database, Database]) -> None:
    """Verify lexical_search applies inline filters and returns empty when code_db is None."""
    db, code_db = mock_dbs
    now = time.time()

    fid_doc = db.insert_file(Candidate("/docs/architecture.md", FileKind.DOC, 200, now - 100, 1, 1))
    db.insert_segment(
        fid_doc,
        SegmentKind.CHUNK,
        Modality.TEXT,
        excerpt="pipeline architecture overview",
        body="pipeline architecture overview",
        original="Pipeline Architecture overview",
    )
    fid_pdf = db.insert_file(Candidate("/docs/architecture.pdf", FileKind.PDF, 500, now - 200, 2, 1))
    db.insert_segment(
        fid_pdf,
        SegmentKind.PAGE,
        Modality.TEXT,
        loc=3.0,
        excerpt="pipeline architecture diagram",
        body="pipeline architecture diagram",
        original="Pipeline Architecture diagram",
    )

    fid_code = code_db.insert_file(Candidate("/repo/pipeline.py", FileKind.CODE, 300, now - 50, 3, 1))
    code_db.insert_segment(
        fid_code,
        SegmentKind.LINES,
        Modality.TEXT,
        loc=10.0,
        loc_end=25.0,
        excerpt="def dispatch_stage():",
        body="class Pipeline dispatch_stage run",
        original="class Pipeline:\n    def dispatch_stage(self): pass",
        focus=12.0,
    )

    # Disabled code_db must return empty ResultSet without falling back to general db
    searcher_no_code = SearchService(db=db, code_db=None)
    assert len(searcher_no_code.lexical_search("code: pipeline")) == 0
    assert len(searcher_no_code.lexical_search("pipeline", is_code=True)) == 0

    # General lexical search with inline filters
    searcher = SearchService(db=db, code_db=code_db)
    res_pdf = searcher.lexical_search("kind:pdf pipeline")
    assert [h.path for h in res_pdf.ranked] == ["/docs/architecture.pdf"]
    assert res_pdf.ranked[0].loc == 3.0
    assert "«" in res_pdf.ranked[0].excerpt

    # Code lexical search matching a LINES segment preserves stored segment focus
    res_code = searcher.lexical_search("code: dispatch_stage")
    assert [h.path for h in res_code.ranked] == ["/repo/pipeline.py"]
    assert res_code.ranked[0].focus == 12.0

    # Pure filter lexical search
    res_ext = searcher.lexical_search("ext:md")
    assert [h.path for h in res_ext.ranked] == ["/docs/architecture.md"]


def test_search_uses_stored_segment_focus_fallback_and_defers_snippets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify seg_meta.focus acts as code focus fallback and extract_snippet is deferred to final hits."""
    db = Database(tmp_path / "index.sqlite")
    code_db = Database(tmp_path / "code.sqlite")

    for i in range(6):
        fid = db.insert_file(Candidate(f"/docs/item_{i}.txt", FileKind.DOC, 100, float(i + 1), i + 1, 1))
        db.insert_segment(
            fid,
            SegmentKind.CHUNK,
            Modality.TEXT,
            excerpt=f"telemetry report {i}",
            body=f"telemetry report {i}",
            original=f"Telemetry Report {i}",
        )

    snippet_calls = 0

    def counting_snippet(text: str, terms: list[str], max_words: int = 12) -> str | None:
        nonlocal snippet_calls
        snippet_calls += 1
        return extract_snippet(text, terms, max_words=max_words)

    monkeypatch.setattr("semsearch.services.searcher.extract_snippet", counting_snippet)
    monkeypatch.setattr("semsearch.core.database.extract_snippet", counting_snippet)

    searcher = SearchService(db=db, code_db=code_db)
    res = searcher.search("telemetry report", limit=2)
    assert len(res.ranked) == 2
    assert snippet_calls <= 2

    # Code segment matched semantically (no literal word hit) falls back to stored seg_meta.focus
    rng = np.random.default_rng(11)
    q_vec = l2_normalize(rng.standard_normal(DIM).astype(np.float32))
    bg_vec = l2_normalize(rng.standard_normal(DIM).astype(np.float32))
    for idx in range(15):
        bg_fid = code_db.insert_file(Candidate(str(tmp_path / f"bg_{idx}.py"), FileKind.CODE, 50, 1.0, idx + 1, 1))
        code_db.insert_segment(
            bg_fid,
            SegmentKind.LINES,
            Modality.TEXT,
            loc=1.0,
            loc_end=5.0,
            excerpt="unrelated helper",
            vec=float16_to_bytes(bg_vec),
            body="unrelated helper",
            original="unrelated helper",
        )

    fid_code = code_db.insert_file(Candidate(str(tmp_path / "metrics.py"), FileKind.CODE, 100, 1.0, 99, 1))
    code_db.insert_segment(
        fid_code,
        SegmentKind.LINES,
        Modality.TEXT,
        loc=10.0,
        loc_end=30.0,
        excerpt="def record_counter():",
        vec=float16_to_bytes(q_vec),
        body="def record_counter(): pass",
        original="def record_counter(): pass",
        focus=18.0,
    )

    class SemanticMockOllama(OllamaClient):
        def embed(
            self,
            _model: str,
            _inputs: list[str | dict[str, str]],
            *,
            truncate: bool = True,
            num_ctx: int | None = None,
            keep_alive: int | str | None = None,
        ) -> list[list[float]]:
            del truncate, num_ctx, keep_alive
            return [q_vec.tolist()]

    semantic_searcher = SearchService(db=db, code_db=code_db, ollama=SemanticMockOllama())
    res_code = semantic_searcher.search("code: instrumentation observability")
    assert len(res_code.ranked) == 1
    assert res_code.ranked[0].path == str(tmp_path / "metrics.py")
    assert res_code.ranked[0].focus == 18.0


def test_vector_search_uses_model_aware_query_prompts(mock_dbs: tuple[Database, Database]) -> None:
    """Verify EmbeddingGemma receives task-formatted queries while non-Gemma models receive clean queries."""
    db, code_db = mock_dbs
    recorded_prompts: list[tuple[str, list[str | dict[str, str]]]] = []

    class RecordingOllama(OllamaClient):
        def uses_gemma_prompts(self, model: str) -> bool:
            return "embeddinggemma" in model.lower()

        def embed(
            self,
            model: str,
            inputs: list[str | dict[str, str]],
            *,
            truncate: bool = True,
            num_ctx: int | None = None,
            keep_alive: int | str | None = None,
        ) -> list[list[float]]:
            del truncate, num_ctx, keep_alive
            recorded_prompts.append((model, list(inputs)))
            return []

    ollama = RecordingOllama()

    gemma_searcher = SearchService(db=db, code_db=code_db, ollama=ollama, model="embeddinggemma-2")
    gemma_searcher.search("quarterly report")
    gemma_searcher.search("code: binary search")

    clip_searcher = SearchService(db=db, code_db=code_db, ollama=ollama, model="clip-vit:latest")
    clip_searcher.search("quarterly report")
    clip_searcher.search("code: binary search")

    assert recorded_prompts == [
        ("embeddinggemma-2", ["task: search result | query: quarterly report"]),
        ("embeddinggemma-2", ["task: code retrieval | query: binary search"]),
        ("clip-vit:latest", ["quarterly report"]),
        ("clip-vit:latest", ["binary search"]),
    ]
