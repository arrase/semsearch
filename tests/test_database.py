"""Unit tests for SQLite WAL database and FTS indexing."""

import threading
from pathlib import Path

import numpy as np
import pytest

from semsearch.core.database import Database
from semsearch.core.types import (
    Candidate,
    FileKind,
    FileState,
    Modality,
    SegmentKind,
)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    """Create a temporary database instance."""
    db_path = tmp_path / "test_index.sqlite"
    database = Database(db_path)
    yield database
    database.close()


def test_schema_initialization(db: Database):
    """Verify pragmas, user_version, and table existence."""
    cursor = db._conn.cursor()
    cursor.execute("PRAGMA user_version;")
    assert cursor.fetchone()[0] == 5

    cursor.execute("PRAGMA foreign_keys;")
    assert cursor.fetchone()[0] == 1

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row[0] for row in cursor.fetchall()}
    assert "meta" in tables
    assert "files" in tables
    assert "segments" in tables
    assert "fts" in tables


def test_meta_operations(db: Database):
    """Verify get and set metadata operations."""
    assert db.get_meta("fingerprint") is None
    db.set_meta("fingerprint", "ollama-gemma2")
    assert db.get_meta("fingerprint") == "ollama-gemma2"
    db.set_meta("fingerprint", None)
    assert db.get_meta("fingerprint") is None


def test_insert_and_find_file(db: Database):
    """Verify inserting file candidate and finding by path or inode."""
    candidate = Candidate(
        path="/home/user/docs/notes.md",
        kind=FileKind.DOC,
        size=1024,
        mtime=1700000000.0,
        inode=1001,
        device=2001,
    )
    file_id = db.insert_file(candidate)
    assert file_id > 0

    record_by_path = db.find_by_path("/home/user/docs/notes.md")
    assert record_by_path is not None
    assert record_by_path.id == file_id
    assert record_by_path.kind == FileKind.DOC
    assert record_by_path.state == FileState.PENDING

    record_by_inode = db.find_by_inode(2001, 1001)
    assert record_by_inode is not None
    assert record_by_inode.id == file_id

    record_by_id = db.find_by_id(file_id)
    assert record_by_id is not None
    assert record_by_id.id == file_id
    assert db.find_by_id(999999) is None

    # Check that name segment was created
    cursor = db._conn.cursor()
    cursor.execute("SELECT kind, modality FROM segments WHERE file = ?", (file_id,))
    segments = cursor.fetchall()
    assert len(segments) == 1
    assert segments[0][0] == "name"
    assert segments[0][1] == "none"

    # Check that FTS entry was created for name
    fts_results = db.fts_search("notes")
    assert len(fts_results) == 1
    assert fts_results[0]["file_id"] == file_id
    assert fts_results[0]["name"] == "notes.md"


def test_list_pending_order_and_limit(db: Database):
    """Verify pending files listing with priority ordering."""
    c1 = Candidate("/tmp/doc.txt", FileKind.DOC, 100, 10.0, 1, 1)
    c2 = Candidate("/tmp/shot.png", FileKind.SCREENSHOT, 200, 20.0, 2, 1)
    c3 = Candidate("/tmp/pic.jpg", FileKind.IMAGE, 300, 30.0, 3, 1)

    db.insert_file(c1)
    db.insert_file(c2)
    db.insert_file(c3)

    pending = db.list_pending()
    assert len(pending) == 3
    # Screenshot comes before image, image before doc
    assert pending[0].kind == FileKind.SCREENSHOT
    assert pending[1].kind == FileKind.IMAGE
    assert pending[2].kind == FileKind.DOC

    # Limit check
    assert len(db.list_pending(limit=2)) == 2


def test_insert_segment_and_vector_retrieval(db: Database):
    """Verify segment insertion and vector matrix retrieval."""
    candidate = Candidate("/tmp/paper.pdf", FileKind.PDF, 2048, 10.0, 10, 1)
    file_id = db.insert_file(candidate)

    # 768-dim float16 vector
    raw_vec = np.ones(768, dtype=np.float16).tobytes()

    seg_id = db.insert_segment(
        file_id=file_id,
        kind=SegmentKind.PAGE,
        modality=Modality.TEXT,
        loc=1.0,
        loc_end=1.0,
        excerpt="Introduction to machine learning",
        vec=raw_vec,
        name="",
        body="Introduction to machine learning and neural networks",
        original="Introduction to machine learning and neural networks",
    )
    assert seg_id > 0

    seg_ids, file_ids, modalities, matrix = db.get_all_vectors()
    assert len(seg_ids) == 1
    assert seg_ids[0] == seg_id
    assert file_ids[0] == file_id
    assert modalities[0] == Modality.TEXT
    assert matrix.shape == (1, 768)
    assert matrix[0, 0] == 1.0


def test_move_file(db: Database):
    """Verify moving file updates path and FTS name row without altering segments or vectors."""
    candidate = Candidate("/tmp/old_dir/photo.png", FileKind.IMAGE, 500, 10.0, 55, 1)
    file_id = db.insert_file(candidate)

    raw_vec = np.zeros(768, dtype=np.float16).tobytes()
    seg_id = db.insert_segment(
        file_id=file_id,
        kind=SegmentKind.IMAGE,
        modality=Modality.IMAGE,
        loc=0.0,
        loc_end=0.0,
        excerpt="sunset",
        vec=raw_vec,
        name="",
        body="sunset over mountains",
        original="",
    )

    db.move_file(file_id, "/tmp/new_dir/sunset_final.png")

    rec = db.find_by_path("/tmp/new_dir/sunset_final.png")
    assert rec is not None
    assert rec.id == file_id
    assert db.find_by_path("/tmp/old_dir/photo.png") is None

    # Verify segment still exists with vector
    seg_ids, _, _, _ = db.get_all_vectors()
    assert seg_id in seg_ids

    # Verify FTS finds the new name and does not find old name
    assert len(db.fts_search("sunset_final")) == 1
    assert len(db.fts_search("photo")) == 0


def test_requeue_file(db: Database):
    """Verify requeuing drops content segments but keeps name segment."""
    candidate = Candidate("/tmp/code.py", FileKind.CODE, 100, 10.0, 77, 1)
    file_id = db.insert_file(candidate)

    db.insert_segment(
        file_id=file_id,
        kind=SegmentKind.LINES,
        modality=Modality.TEXT,
        loc=1.0,
        loc_end=10.0,
        excerpt="def hello(): pass",
        vec=np.zeros(768, dtype=np.float16).tobytes(),
        name="",
        body="def hello(): pass",
        original="",
    )

    db.update_file_state(file_id, FileState.DONE, detail="indexed")
    assert db.find_by_path("/tmp/code.py").state == FileState.DONE

    # Requeue with new size and mtime
    updated_candidate = Candidate("/tmp/code.py", FileKind.CODE, 150, 20.0, 77, 1)
    db.requeue_file(file_id, updated_candidate)

    updated_rec = db.find_by_path("/tmp/code.py")
    assert updated_rec.state == FileState.PENDING
    assert updated_rec.size == 150
    assert updated_rec.mtime == 20.0

    # Content segment should be gone, only name segment remains
    cursor = db._conn.cursor()
    cursor.execute("SELECT kind FROM segments WHERE file = ?", (file_id,))
    kinds = [r[0] for r in cursor.fetchall()]
    assert kinds == ["name"]


def test_cascade_delete_file(db: Database):
    """Verify deleting file cascades to segments and FTS."""
    candidate = Candidate("/tmp/doc.pdf", FileKind.PDF, 200, 10.0, 88, 1)
    file_id = db.insert_file(candidate)

    seg_id = db.insert_segment(
        file_id=file_id,
        kind=SegmentKind.PAGE,
        modality=Modality.TEXT,
        loc=1.0,
        loc_end=1.0,
        excerpt="confidential report",
        vec=None,
        name="",
        body="confidential report content",
        original="",
    )

    assert len(db.fts_search("confidential")) == 1

    db.delete_file(file_id)

    assert db.find_by_path("/tmp/doc.pdf") is None
    cursor = db._conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM segments WHERE file = ?", (file_id,))
    assert cursor.fetchone()[0] == 0
    cursor.execute("SELECT COUNT(*) FROM fts WHERE rowid = ?", (seg_id,))
    assert cursor.fetchone()[0] == 0
    assert len(db.fts_search("confidential")) == 0


def test_database_stats(db: Database):
    """Verify stats counts."""
    c = Candidate("/tmp/test.txt", FileKind.DOC, 50, 10.0, 99, 1)
    file_id = db.insert_file(c)
    db.insert_segment(
        file_id=file_id,
        kind=SegmentKind.CHUNK,
        modality=Modality.TEXT,
        loc=0.0,
        loc_end=0.0,
        excerpt="sample",
        vec=np.zeros(768, dtype=np.float16).tobytes(),
        name="",
        body="sample chunk",
        original="",
    )

    counts = db.stats()
    assert counts["files"] == 1
    assert counts["pending"] == 1
    assert counts["segments"] == 2  # name segment + chunk segment
    assert counts["vectors"] == 1


def test_find_files_under(db: Database):
    """Verify querying files under a specific root prefix."""
    c1 = Candidate("/data/proj1/src/main.py", FileKind.CODE, 100, 10.0, 1, 1)
    c2 = Candidate("/data/proj1/README.md", FileKind.DOC, 200, 10.0, 2, 1)
    c3 = Candidate("/data/proj2/app.py", FileKind.CODE, 300, 10.0, 3, 1)
    c4 = Candidate("/data/proj1_other/notes.txt", FileKind.DOC, 400, 10.0, 4, 1)

    db.insert_file(c1)
    db.insert_file(c2)
    db.insert_file(c3)
    db.insert_file(c4)

    proj1_files = db.find_files_under("/data/proj1")
    proj1_paths = {f.path for f in proj1_files}
    assert proj1_paths == {"/data/proj1/src/main.py", "/data/proj1/README.md"}

    single_file = db.find_files_under("/data/proj1/README.md")
    assert len(single_file) == 1
    assert single_file[0].path == "/data/proj1/README.md"

    proj2_files = db.find_files_under("/data/proj2")
    assert len(proj2_files) == 1
    assert proj2_files[0].path == "/data/proj2/app.py"

    c5 = Candidate("/data/proj_special/sub/file1.txt", FileKind.DOC, 100, 10.0, 5, 1)
    c6 = Candidate("/data/proj-special/sub/file2.txt", FileKind.DOC, 100, 10.0, 6, 1)
    db.insert_file(c5)
    db.insert_file(c6)

    special_files = db.find_files_under("/data/proj_special")
    assert len(special_files) == 1
    assert special_files[0].path == "/data/proj_special/sub/file1.txt"

    c7 = Candidate("/data/proj%special/sub/file3.txt", FileKind.DOC, 100, 10.0, 7, 1)
    c8 = Candidate("/data/projOTHERspecial/sub/file4.txt", FileKind.DOC, 100, 10.0, 8, 1)
    c9 = Candidate(r"/data/proj\special/sub/file5.txt", FileKind.DOC, 100, 10.0, 9, 1)
    db.insert_file(c7)
    db.insert_file(c8)
    db.insert_file(c9)

    percent_files = db.find_files_under("/data/proj%special")
    assert len(percent_files) == 1
    assert percent_files[0].path == "/data/proj%special/sub/file3.txt"

    backslash_files = db.find_files_under(r"/data/proj\special")
    assert len(backslash_files) == 1
    assert backslash_files[0].path == r"/data/proj\special/sub/file5.txt"

    all_files = db.find_files_under("/")
    assert len(all_files) == 9


def test_fts_search_snippet_highlighting(db: Database):
    """Verify fts_search returns snippet with highlighted text markers."""
    candidate = Candidate("/tmp/document.txt", FileKind.DOC, 100, 10.0, 1, 1)
    file_id = db.insert_file(candidate)

    db.insert_segment(
        file_id=file_id,
        kind=SegmentKind.CHUNK,
        modality=Modality.TEXT,
        loc=0.0,
        loc_end=0.0,
        excerpt="The quick brown fox jumps over the lazy dog",
        vec=None,
        name="",
        body="The quick brown fox jumps over the lazy dog",
        original="The quick brown fox jumps over the lazy dog",
    )

    results = db.fts_search("brown fox")
    assert len(results) == 1
    hit = results[0]
    assert "snippet" in hit
    assert "«brown»" in hit["snippet"]
    assert "«fox»" in hit["snippet"]


def test_fts_search_includes_loc_and_excerpt(db: Database):
    """Verify fts_search includes loc and excerpt fields with proper fallbacks and negative BM25 rank."""
    candidate = Candidate("/tmp/manual.pdf", FileKind.PDF, 2048, 10.0, 1, 1)
    file_id = db.insert_file(candidate)

    # Segment with specific loc and excerpt
    db.insert_segment(
        file_id=file_id,
        kind=SegmentKind.PAGE,
        modality=Modality.TEXT,
        loc=15.0,
        loc_end=15.0,
        excerpt="Page 15 content about system diagnostics",
        vec=None,
        name="manual.pdf",
        body="Page 15 content about system diagnostics",
        original="",
    )

    results = db.fts_search("diagnostics")
    assert len(results) == 1
    hit = results[0]
    assert "loc" in hit
    assert hit["loc"] == 15.0
    assert "excerpt" in hit
    assert hit["excerpt"] == "Page 15 content about system diagnostics"
    assert "rank" in hit
    assert hit["rank"] < 0  # SQLite FTS5 bm25 returns negative values

    # Segment with None loc and empty excerpt
    db.insert_segment(
        file_id=file_id,
        kind=SegmentKind.CHUNK,
        modality=Modality.TEXT,
        loc=None,
        loc_end=None,
        excerpt=None,
        vec=None,
        name="manual.pdf",
        body="Troubleshooting steps for system recovery",
        original="",
    )

    results_null = db.fts_search("troubleshooting")
    assert len(results_null) == 1
    hit_null = results_null[0]
    assert hit_null["loc"] == 0.0
    assert hit_null["excerpt"] == ""

    # Orphaned FTS row with no corresponding segment
    cursor = db._conn.cursor()
    cursor.execute("INSERT INTO fts (rowid, name, body, original) VALUES (9999, 'orphan.pdf', 'system orphan', '')")
    db._conn.commit()

    results_orphan = db.fts_search("orphan")
    assert len(results_orphan) == 1
    hit_orphan = results_orphan[0]
    assert hit_orphan["segment_id"] == 9999
    assert hit_orphan["file_id"] is None
    assert hit_orphan["loc"] == 0.0
    assert hit_orphan["excerpt"] == ""


def test_get_all_vectors_with_scale_and_file_segments(db: Database):
    """Verify get_all_vectors_with_scale marks only the first segment per (file, modality)."""
    c1 = Candidate("/tmp/multi.md", FileKind.DOC, 500, 10.0, 1, 1)
    fid1 = db.insert_file(c1)
    vec_bytes = np.ones(768, dtype=np.float16).tobytes()

    db.insert_segment(fid1, SegmentKind.CHUNK, Modality.TEXT, 0.0, 1.0, "chunk 0", vec_bytes, "", "chunk zero", "")
    db.insert_segment(fid1, SegmentKind.CHUNK, Modality.TEXT, 1.0, 2.0, "chunk 1", vec_bytes, "", "chunk one", "")

    seg_ids, _file_ids, _modalities, _matrix, sets_scale = db.get_all_vectors_with_scale()
    assert len(seg_ids) == 2
    assert sets_scale == [True, False]

    segs = db.get_file_segments(fid1)
    assert len(segs) == 2
    assert segs[0][1] == SegmentKind.CHUNK
    assert segs[0][5] == "chunk zero"

    db.update_file_kind(fid1, FileKind.SCREENSHOT)
    assert db.find_by_id(fid1).kind == FileKind.SCREENSHOT


def test_fts_malformed_query_and_thread_safety(db: Database):
    """Verify malformed FTS queries return empty list and concurrent threads do not fail."""
    assert db.fts_search('unclosed "quote') == []

    errors: list[Exception] = []

    def worker(idx: int) -> None:
        try:
            cid = db.insert_file(Candidate(f"/tmp/f_{idx}.txt", FileKind.DOC, 10, float(idx), idx + 100, 1))
            db.insert_segment(
                cid, SegmentKind.CHUNK, Modality.TEXT, 0.0, 0.0, f"body {idx}", None, f"f_{idx}.txt", f"body {idx}", ""
            )
            db.fts_search("body")
            db.stats()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert db.stats()["files"] == 8


def test_shrink_memory_thread_safe_execution(db: Database):
    """Verify shrink_memory executes PRAGMA shrink_memory safely under lock without affecting stored records."""
    cid = db.insert_file(Candidate("/tmp/shrink.txt", FileKind.DOC, 42, 10.0, 500, 1))
    db.insert_segment(
        cid,
        SegmentKind.CHUNK,
        Modality.TEXT,
        0.0,
        1.0,
        "memory test",
        np.ones(768, dtype=np.float16).tobytes(),
        "shrink.txt",
        "memory test content",
        "",
    )

    errors: list[Exception] = []

    def worker() -> None:
        try:
            for _ in range(5):
                db.shrink_memory()
                db.stats()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    rec = db.find_by_id(cid)
    assert rec is not None
    assert rec.path == "/tmp/shrink.txt"
    assert len(db.fts_search("memory")) == 1
