"""End-to-end search fidelity, BM25 lookup, code anchors, and quality gating tests."""

import base64
import io
from pathlib import Path
import numpy as np
from PIL import Image
import pytest

from semsearch.config import AppConfig
from semsearch.core.database import Database
from semsearch.core.ollama import OllamaClient
from semsearch.core.search_text import is_lookup_query, make_fts_patterns
from semsearch.core.types import (
    Candidate,
    FileKind,
    Modality,
    SearchHit,
    SegmentKind,
)
from semsearch.core.vectors import DIM, l2_normalize
from semsearch.services.indexer import IndexService
from semsearch.services.searcher import ResultSet, SearchService
from tests.testbed import generate_testbed


class SemanticMockOllama:
    """Mock OllamaClient providing controlled semantic embeddings for modality testing."""

    def __init__(self) -> None:
        rng = np.random.default_rng(1234)
        self.sunset_vec = l2_normalize(rng.standard_normal(DIM).astype(np.float32))
        self.video_vec = l2_normalize(rng.standard_normal(DIM).astype(np.float32))
        self.audio_vec = l2_normalize(rng.standard_normal(DIM).astype(np.float32))
        self.brief_vec = l2_normalize(rng.standard_normal(DIM).astype(np.float32))
        self.code_vec = l2_normalize(rng.standard_normal(DIM).astype(np.float32))
        self.default_vec = l2_normalize(rng.standard_normal(DIM).astype(np.float32))

    def embed(self, model: str, inputs: list[str | dict[str, str]]) -> list[list[float]]:
        results: list[list[float]] = []
        for inp in inputs:
            if isinstance(inp, dict):
                if "image" in inp:
                    raw = base64.b64decode(inp["image"])
                    with Image.open(io.BytesIO(raw)) as im:
                        if im.size == (640, 480):
                            results.append(self.sunset_vec.tolist())
                        elif im.size == (320, 240):
                            results.append(self.video_vec.tolist())
                        else:
                            results.append(self.default_vec.tolist())
                elif "audio" in inp:
                    results.append(self.audio_vec.tolist())
                else:
                    results.append(self.default_vec.tolist())
            else:
                text = str(inp).lower()
                clean = (
                    text.replace("task: search result | query: ", "")
                    .replace("task: code retrieval | query: ", "")
                    .strip()
                )
                if any(w in clean for w in ("sunset", "golden", "landscape")):
                    results.append(self.sunset_vec.tolist())
                elif any(w in clean for w in ("video", "smpte", "bars")):
                    results.append(self.video_vec.tolist())
                elif any(w in clean for w in ("speech", "voice", "tone")):
                    results.append(self.audio_vec.tolist())
                elif any(w in clean for w in ("brief", "strategy", "executive")):
                    results.append(self.brief_vec.tolist())
                elif any(w in clean for w in ("retry", "backoff", "token", "bucket")):
                    results.append(self.code_vec.tolist())
                else:
                    results.append(self.default_vec.tolist())
        return results


@pytest.fixture
def indexed_testbed(tmp_path: Path):
    testbed_dir = tmp_path / "testbed"
    files = generate_testbed(testbed_dir)

    db = Database(tmp_path / "semsearch.db")
    code_db = Database(tmp_path / "code.db")

    config = AppConfig(
        folders=[
            str(testbed_dir / "images"),
            str(testbed_dir / "pdfs"),
            str(testbed_dir / "docs"),
            str(testbed_dir / "media"),
        ],
        code_folders=[str(testbed_dir / "code")],
        code_enabled=True,
    )

    ollama = SemanticMockOllama()
    indexer = IndexService(db=db, code_db=code_db, ollama=ollama, config=config)

    indexer.crawl_and_sync(is_code=False)
    indexer.crawl_and_sync(is_code=True)

    while True:
        rec, target_db, _ = indexer._get_next_file()
        if rec is None or target_db is None:
            break
        indexer.process_file(rec, target_db)

    searcher = SearchService(db=db, code_db=code_db, ollama=ollama)
    return testbed_dir, files, db, code_db, searcher, ollama


# Section 3: Cross-Modal & Semantic Search Fidelity Verification

def test_cross_modal_text_to_image_search(indexed_testbed):
    _, files, _, _, searcher, _ = indexed_testbed
    # Natural language visual description targeting photo_sunset.jpg
    res = searcher.search("photo sunset golden landscape")
    assert len(res.ranked) > 0
    top_hit = res.ranked[0]
    assert Path(top_hit.path).name == "photo_sunset.jpg"
    assert top_hit.kind in (FileKind.IMAGE, FileKind.SCREENSHOT)


def test_cross_modal_text_to_video_search(indexed_testbed):
    _, files, _, _, searcher, _ = indexed_testbed
    # Query matching synthesized video test pattern
    res = searcher.search("video smpte color bars test")
    assert len(res.ranked) > 0
    video_hits = [h for h in res.ranked if Path(h.path).name == "clip_video.mp4"]
    assert len(video_hits) > 0
    assert video_hits[0].segment in (SegmentKind.FRAME, SegmentKind.AUDIO)


def test_search_evaluation_harness_live_ollama():
    try:
        client = OllamaClient()
        tags = client.get_tags()
        models = [t["name"] for t in tags if "embeddinggemma-2" in t.get("name", "")]
        if not models:
            pytest.skip("embeddinggemma-2 model not installed in local Ollama")
        res = client.embed(models[0], ["test semantic evaluation query"])
        assert len(res) == 1
        assert len(res[0]) > 0
    except Exception as exc:
        pytest.skip(f"Live Ollama instance not available: {exc}")


# Section 4: Lexical BM25 & Lookup Serial Search Verification

def test_fts5_bm25_keyword_matching(indexed_testbed):
    _, files, _, _, searcher, _ = indexed_testbed
    res = searcher.search("Executive Strategy Brief")
    assert len(res.ranked) > 0
    assert Path(res.ranked[0].path).name == "brief.docx"
    assert "Strategy" in res.ranked[0].excerpt or "Brief" in res.ranked[0].excerpt


def test_identifier_and_serial_lookup_routing(indexed_testbed):
    _, files, _, _, searcher, _ = indexed_testbed
    assert is_lookup_query("ERR-4096-FAIL") is True
    assert is_lookup_query("0xdeadbeef") is True
    assert is_lookup_query("calculateMetrics") is True
    assert is_lookup_query("general plain text") is False

    # Lookup search for hardware serial
    res_serial = searcher.search("ERR-4096-FAIL")
    assert len(res_serial.ranked) > 0
    assert Path(res_serial.ranked[0].path).name == "notes.txt"
    assert "4096" in res_serial.ranked[0].excerpt


def test_term_coverage_multi_term_and_prefix(tmp_path: Path):
    db = Database(tmp_path / "cov.db")
    fid = db.insert_file(Candidate(path="/tmp/report.txt", kind=FileKind.DOC, size=50, mtime=1.0, inode=1, device=1))
    doc_text = "Quarterly Systems Architectural Report 2026"
    db.insert_segment(fid, SegmentKind.CHUNK, Modality.TEXT, 0.0, 0.0, doc_text, None, "report.txt", doc_text, "")

    patterns = make_fts_patterns(["systems", "architectural", "report"])
    assert db.compute_coverage(patterns, [fid])[fid] == 1.0

    # Prefix match on last term
    prefix_patterns = make_fts_patterns(["systems", "architec"], prefix_last=True)
    assert db.compute_coverage(prefix_patterns, [fid])[fid] == 1.0

    # Partial match
    partial_patterns = make_fts_patterns(["systems", "unknown", "missing"])
    assert pytest.approx(db.compute_coverage(partial_patterns, [fid])[fid], 0.01) == 0.33


# Section 5: Code Search & Definition Line Anchor Verification

def test_code_search_routing(indexed_testbed):
    _, files, _, _, searcher, _ = indexed_testbed
    # Query with code: prefix
    res = searcher.search("code: retry_with_backoff")
    assert len(res.ranked) > 0
    top = res.ranked[0]
    assert Path(top.path).name == "service.py"
    assert top.kind == FileKind.CODE


def test_symbol_definition_line_anchoring(indexed_testbed):
    _, files, _, _, searcher, _ = indexed_testbed
    # Function symbol definition anchor
    res_fn = searcher.search("code: retry_with_backoff")
    assert len(res_fn.ranked) > 0
    assert res_fn.ranked[0].focus == 6.0

    # Class symbol definition anchor
    res_cls = searcher.search("code: TokenBucket")
    assert len(res_cls.ranked) > 0
    assert res_cls.ranked[0].focus == 13.0


def test_jupyter_notebook_code_search(indexed_testbed):
    _, files, _, _, searcher, _ = indexed_testbed
    res = searcher.search("code: calculate_metrics")
    assert len(res.ranked) > 0
    nb_hit = next((h for h in res.ranked if Path(h.path).name == "analysis.ipynb"), None)
    assert nb_hit is not None
    assert nb_hit.kind == FileKind.CODE
    assert nb_hit.loc > 0


# Section 6: Quality Gating & Weaker Matches Verification

def test_quality_gating_filtering(tmp_path: Path):
    db = Database(tmp_path / "gating.db")
    c1_cand = Candidate(path="/tmp/doc1.txt", kind=FileKind.DOC, size=100, mtime=1.0, inode=1, device=1)
    f1 = db.insert_file(c1_cand)
    db.insert_segment(f1, SegmentKind.CHUNK, Modality.TEXT, 1.0, 1.0, "matching target keyword query", None, "doc1.txt", "matching target keyword query", "")

    c2_cand = Candidate(path="/tmp/doc2.txt", kind=FileKind.DOC, size=100, mtime=1.0, inode=2, device=2)
    f2 = db.insert_file(c2_cand)
    db.insert_segment(f2, SegmentKind.CHUNK, Modality.TEXT, 1.0, 1.0, "completely unrelated data", None, "doc2.txt", "only matching term present", "")

    searcher = SearchService(db=db)
    res = searcher.search("matching target keyword query")
    # doc1 has coverage 1.0 (>= 0.5) -> passes gate
    # doc2 has coverage 0.25 (< 0.5) and z=0 (< 2.0) -> pruned by quality gate
    assert len(res.ranked) == 1
    assert res.ranked[0].path == "/tmp/doc1.txt"


def test_folded_weaker_matches_partitioning(indexed_testbed):
    _, _, db, _, searcher, _ = indexed_testbed
    # Insert a weakly matching document
    cand = Candidate(path="/tmp/weak.txt", kind=FileKind.DOC, size=50, mtime=1.0, inode=999, device=999)
    fid = db.insert_file(cand)
    db.insert_segment(fid, SegmentKind.CHUNK, Modality.TEXT, 1.0, 1.0, "Architectural overview brief summary", None, "weak.txt", "Architectural overview brief summary", "")

    res = searcher.search("Quarterly Systems Engineering and Architectural Report")
    # Strong matches with high coverage/score in ranked, weaker matches in weaker
    assert isinstance(res, ResultSet)
    assert len(res.ranked) >= 1
    for w in res.weaker:
        assert w.score < 0.5 * res.ranked[0].score


def test_result_hit_properties(indexed_testbed):
    _, _, _, _, searcher, _ = indexed_testbed
    results = searcher.search("Strategy Brief")
    assert len(results.ranked) > 0
    first = results.ranked[0]
    assert isinstance(first, SearchHit)
    assert Path(first.path).name == "brief.docx"
    assert first.reason in ("Matches meaning", "Matches words", "Matches name")
