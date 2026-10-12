"""Unit tests for SQLite WAL database and FTS indexing."""

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

