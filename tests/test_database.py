"""Unit tests for SQLite WAL database, migrations, vector cache, transactions, and FTS indexing."""

import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from semsearch.core.database import SCHEMA_VERSION, Database, SegmentMeta, _folder_text
from semsearch.core.types import (
    Candidate,
    FileKind,
    FileState,
    Modality,
    SegmentKind,
)
from semsearch.core.vectors import MODALITY_TO_CODE


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    """Create a temporary database instance."""
    db_path = tmp_path / "test_index.sqlite"
    database = Database(db_path)
    yield database
    database.close()


def test_schema_initialization(db: Database) -> None:
    """Verify pragmas, user_version v6, tables, indexes, and FTS delete trigger."""
    cursor = db._write_conn.cursor()
    cursor.execute("PRAGMA user_version;")
    assert cursor.fetchone()[0] == SCHEMA_VERSION == 6

    cursor.execute("PRAGMA foreign_keys;")
    assert cursor.fetchone()[0] == 1

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = {row[0] for row in cursor.fetchall()}
    assert {"meta", "files", "segments", "fts"}.issubset(tables)

    cursor.execute("SELECT name FROM sqlite_master WHERE type='index';")
    indexes = {row[0] for row in cursor.fetchall()}
    assert {"files_inode", "files_state", "files_state_mtime", "segments_file", "segments_has_vec"}.issubset(indexes)

    cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger';")
    triggers = {row[0] for row in cursor.fetchall()}
    assert "segments_fts_delete" in triggers


def test_schema_migration_v5_to_v6(tmp_path: Path) -> None:
    """Verify automatic migration from schema v5 populates focus, has_vec, indexes, and trigger."""
    db_path = tmp_path / "legacy_v5.sqlite"
    raw = sqlite3.connect(db_path)
    raw.executescript(
        """
        PRAGMA user_version = 5;
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            size INTEGER NOT NULL,
            mtime REAL NOT NULL,
            inode INTEGER NOT NULL,
            device INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',
            detail TEXT,
            info TEXT,
            indexed_at REAL
        );
        CREATE TABLE segments (
            id INTEGER PRIMARY KEY,
            file INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            modality TEXT NOT NULL,
            loc REAL,
            loc_end REAL,
            excerpt TEXT,
            vec BLOB
        );
        CREATE VIRTUAL TABLE fts USING fts5(name, body, original UNINDEXED);
        INSERT INTO files (id, path, kind, size, mtime, inode, device, state)
        VALUES (1, '/tmp/legacy.pdf', 'pdf', 100, 1.0, 1, 1, 'done');
        """
    )
    vec_blob = np.ones(512, dtype=np.float16).tobytes()
    raw.execute(
        "INSERT INTO segments (id, file, kind, modality, loc, loc_end, excerpt, vec) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (10, 1, "page", "text", 1.0, 1.0, "legacy page", vec_blob),
    )
    raw.execute("INSERT INTO fts (rowid, name, body, original) VALUES (10, 'legacy', 'legacy page', '')")
    raw.commit()
    raw.close()

    with Database(db_path) as migrated:
        version = migrated._write_conn.execute("PRAGMA user_version;").fetchone()[0]
        assert version == 6
        assert migrated.stats()["vectors"] == 1
        snap = migrated.get_vector_snapshot()
        assert snap.matrix.shape == (1, 512)
        seg_map = migrated.get_segments_by_ids([10])
        assert seg_map[10].focus == 0.0

        # Deleting segment triggers automatic FTS cleanup
        migrated.delete_non_name_segments(1)
        assert migrated.fts_search("legacy") == []


def test_meta_operations(db: Database) -> None:
    """Verify get and set metadata operations."""
    assert db.get_meta("fingerprint") is None
    db.set_meta("fingerprint", "ollama-gemma2")
    assert db.get_meta("fingerprint") == "ollama-gemma2"
    db.set_meta("fingerprint", None)
    assert db.get_meta("fingerprint") is None


def test_folder_text_uses_relative_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify _folder_text strips only real Path.home() ancestry without prefix collisions."""
    fake_home = Path("/home/alice")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: fake_home))

    assert _folder_text(Path("/home/alice/Documents/Taxes/file.pdf")) == "Documents Taxes"
    assert _folder_text(Path("/home/alice/file.pdf")) == ""
    assert _folder_text(Path("/home/alice2/docs/file.pdf")) == "home alice2 docs"


def test_insert_and_find_file(db: Database) -> None:
    """Verify inserting file candidate and finding by path, inode, ID, or batch IDs."""
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

    batch = db.find_by_ids([file_id, 999999, file_id])
    assert set(batch.keys()) == {file_id}
    assert batch[file_id].path == "/home/user/docs/notes.md"
    assert db.find_by_ids([]) == {}

    cursor = db._write_conn.cursor()
    cursor.execute("SELECT kind, modality FROM segments WHERE file = ?", (file_id,))
    segments = cursor.fetchall()
    assert segments == [("name", "none")]

    fts_results = db.fts_search("notes")
    assert len(fts_results) == 1
    assert fts_results[0]["file_id"] == file_id
    assert fts_results[0]["name"] == "notes.md"


def test_list_pending_order_and_limit(db: Database) -> None:
    """Verify pending files listing with priority ordering and parameterized limit."""
    c1 = Candidate("/tmp/doc.txt", FileKind.DOC, 100, 10.0, 1, 1)
    c2 = Candidate("/tmp/shot.png", FileKind.SCREENSHOT, 200, 20.0, 2, 1)
    c3 = Candidate("/tmp/pic.jpg", FileKind.IMAGE, 300, 30.0, 3, 1)

    db.insert_file(c1)
    db.insert_file(c2)
    db.insert_file(c3)

    pending = db.list_pending()
    assert len(pending) == 3
    assert pending[0].kind == FileKind.SCREENSHOT
    assert pending[1].kind == FileKind.IMAGE
    assert pending[2].kind == FileKind.DOC

    assert len(db.list_pending(limit=2)) == 2


def test_insert_segment_vector_snapshot_cache_and_dynamic_dim(db: Database) -> None:
    """Verify VectorSnapshot caching, invalidation, dynamic dimension, focus, and fts.original dedup."""
    candidate = Candidate("/tmp/paper.pdf", FileKind.PDF, 2048, 10.0, 10, 1)
    file_id = db.insert_file(candidate)

    # 1024-dim float16 vector (non-768 dynamic dimension)
    raw_vec = np.ones(1024, dtype=np.float16).tobytes()

    seg_id = db.insert_segment(
        file_id=file_id,
        kind=SegmentKind.PAGE,
        modality=Modality.TEXT,
        loc=1.0,
        loc_end=2.0,
        excerpt="Introduction to machine learning",
        vec=raw_vec,
        name="",
        body="Introduction to machine learning and neural networks",
        original="Introduction to machine learning and neural networks",
        focus=0.75,
    )
    assert seg_id > 0

    # Verify fts.original was deduplicated since original == norm_body
    orig_stored = db._write_conn.execute("SELECT original FROM fts WHERE rowid = ?", (seg_id,)).fetchone()[0]
    assert orig_stored == ""

    snap1 = db.get_vector_snapshot()
    snap2 = db.get_vector_snapshot()
    assert snap1 is snap2  # Cache hit returns exact same immutable snapshot
    assert snap1.segment_ids.tolist() == [seg_id]
    assert snap1.file_ids.tolist() == [file_id]
    assert snap1.modality_codes.tolist() == [MODALITY_TO_CODE[Modality.TEXT]]
    assert snap1.matrix.shape == (1, 1024)
    assert snap1.matrix[0, 0] == 1.0
    assert snap1.matrix.flags.writeable is False

    seg_meta = db.get_segments_by_ids([seg_id, 999999])
    assert seg_meta == {
        seg_id: SegmentMeta(
            id=seg_id,
            kind=SegmentKind.PAGE,
            loc=1.0,
            loc_end=2.0,
            excerpt="Introduction to machine learning",
            focus=0.75,
        )
    }

    # Insert a mismatched-dimension blob and verify snapshot skips it safely without crashing
    bad_vec = np.ones(128, dtype=np.float16).tobytes()
    db.insert_segment(
        file_id=file_id,
        kind=SegmentKind.PAGE,
        modality=Modality.TEXT,
        loc=2.0,
        loc_end=2.0,
        excerpt="mismatched",
        vec=bad_vec,
    )
    snap3 = db.get_vector_snapshot()
    assert snap3 is not snap1  # Cache invalidated by vector insert
    assert snap3.matrix.shape == (1, 1024)


def test_transaction_nesting_and_rollback(db: Database) -> None:
    """Verify nested transactions commit atomically and roll back completely on error."""
    c1 = Candidate("/tmp/tx_a.txt", FileKind.DOC, 10, 1.0, 11, 1)
    c2 = Candidate("/tmp/tx_b.txt", FileKind.DOC, 20, 2.0, 12, 1)

    def failing_batch() -> None:
        with db.transaction():
            fid = db.insert_file(c1)
            db.insert_segment(
                fid,
                SegmentKind.CHUNK,
                Modality.TEXT,
                vec=np.ones(64, dtype=np.float16).tobytes(),
                body="rolled back",
            )
            raise RuntimeError("abort batch")

    with pytest.raises(RuntimeError, match="abort batch"):
        failing_batch()

    assert db.find_by_path("/tmp/tx_a.txt") is None
    assert db.get_vector_snapshot().matrix.size == 0

    with db.transaction():
        db.insert_file(c1)
        db.insert_file(c2)

    assert db.find_by_path("/tmp/tx_a.txt") is not None
    assert db.find_by_path("/tmp/tx_b.txt") is not None


def test_sync_model_metadata_and_reset_all_for_reindex(db: Database) -> None:
    """Verify model/dim metadata triggers full reindex when changed on a populated DB."""
    assert db.sync_model_metadata("embeddinggemma-2") is False
    assert db.get_meta("embedding_model") == "embeddinggemma-2"

    fid = db.insert_file(Candidate("/tmp/report.pdf", FileKind.PDF, 500, 1.0, 20, 1))
    db.insert_segment(
        fid,
        SegmentKind.PAGE,
        Modality.TEXT,
        loc=1.0,
        loc_end=1.0,
        excerpt="quarterly results",
        vec=np.ones(768, dtype=np.float16).tobytes(),
        body="quarterly results",
    )
    db.update_file_state(fid, FileState.DONE, detail="ok", info={"pages": 1})

    # Recording matching dimension does not reset
    assert db.sync_model_metadata("embeddinggemma-2", dim=768) is False
    assert db.get_meta("embedding_dim") == "768"

    # Switching model resets all files and clears non-name segments + vectors
    assert db.sync_model_metadata("nomic-embed-vision", dim=512) is True
    assert db.get_meta("embedding_model") == "nomic-embed-vision"
    assert db.get_meta("embedding_dim") == "512"

    rec = db.find_by_id(fid)
    assert rec is not None
    assert rec.state == FileState.PENDING
    assert rec.detail == ""
    assert rec.info == {}
    assert db.stats()["vectors"] == 0
    assert db.get_vector_snapshot().matrix.size == 0
    assert db.fts_search("quarterly") == []
    assert len(db.fts_search("report")) == 1  # Name segment kept


def test_move_file(db: Database) -> None:
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

    snap = db.get_vector_snapshot()
    assert seg_id in snap.segment_ids.tolist()

    assert len(db.fts_search("sunset_final")) == 1
    assert len(db.fts_search("photo")) == 0


def test_requeue_file_updates_name_fts(db: Database) -> None:
    """Verify requeuing drops content segments and refreshes FTS name/folder when path changes."""
    candidate = Candidate("/tmp/old_folder/code.py", FileKind.CODE, 100, 10.0, 77, 1)
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
    done_rec = db.find_by_path("/tmp/old_folder/code.py")
    assert done_rec is not None
    assert done_rec.state == FileState.DONE

    updated_candidate = Candidate("/tmp/new_folder/renamed.py", FileKind.CODE, 150, 20.0, 77, 1)
    db.requeue_file(file_id, updated_candidate)

    updated_rec = db.find_by_path("/tmp/new_folder/renamed.py")
    assert updated_rec is not None
    assert updated_rec.state == FileState.PENDING
    assert updated_rec.size == 150
    assert updated_rec.mtime == 20.0

    cursor = db._write_conn.cursor()
    cursor.execute("SELECT kind FROM segments WHERE file = ?", (file_id,))
    assert [r[0] for r in cursor.fetchall()] == ["name"]
    assert db.get_vector_snapshot().matrix.size == 0

    assert len(db.fts_search("renamed")) == 1
    assert len(db.fts_search("new_folder")) == 1
    assert len(db.fts_search("old_folder")) == 0


def test_cascade_delete_file(db: Database) -> None:
    """Verify deleting file cascades to segments and FTS via trigger."""
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
    cursor = db._write_conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM segments WHERE file = ?", (file_id,))
    assert cursor.fetchone()[0] == 0
    cursor.execute("SELECT COUNT(*) FROM fts WHERE rowid = ?", (seg_id,))
    assert cursor.fetchone()[0] == 0
    assert len(db.fts_search("confidential")) == 0


def test_database_stats_includes_failed(db: Database) -> None:
    """Verify stats counts files, pending, failed, segments, and indexed vectors."""
    f1 = db.insert_file(Candidate("/tmp/test.txt", FileKind.DOC, 50, 10.0, 99, 1))
    f2 = db.insert_file(Candidate("/tmp/broken.txt", FileKind.DOC, 50, 10.0, 100, 1))
    db.update_file_state(f2, FileState.FAILED, detail="corrupt")
    db.insert_segment(
        file_id=f1,
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
    assert counts["files"] == 2
    assert counts["pending"] == 1
    assert counts["failed"] == 1
    assert counts["segments"] == 3  # 2 name segments + 1 chunk segment
    assert counts["vectors"] == 1


def test_find_files_under(db: Database) -> None:
    """Verify querying files under a specific root prefix with escaped LIKE wildcards."""
    c1 = Candidate("/data/proj1/src/main.py", FileKind.CODE, 100, 10.0, 1, 1)
    c2 = Candidate("/data/proj1/README.md", FileKind.DOC, 200, 10.0, 2, 1)
    c3 = Candidate("/data/proj2/app.py", FileKind.CODE, 300, 10.0, 3, 1)
    c4 = Candidate("/data/proj1_other/notes.txt", FileKind.DOC, 400, 10.0, 4, 1)

    for cand in (c1, c2, c3, c4):
        db.insert_file(cand)

    proj1_paths = {f.path for f in db.find_files_under("/data/proj1")}
    assert proj1_paths == {"/data/proj1/src/main.py", "/data/proj1/README.md"}

    single_file = db.find_files_under("/data/proj1/README.md")
    assert len(single_file) == 1
    assert single_file[0].path == "/data/proj1/README.md"

    c5 = Candidate("/data/proj_special/sub/file1.txt", FileKind.DOC, 100, 10.0, 5, 1)
    c6 = Candidate("/data/proj-special/sub/file2.txt", FileKind.DOC, 100, 10.0, 6, 1)
    c7 = Candidate("/data/proj%special/sub/file3.txt", FileKind.DOC, 100, 10.0, 7, 1)
    c8 = Candidate("/data/projOTHERspecial/sub/file4.txt", FileKind.DOC, 100, 10.0, 8, 1)
    c9 = Candidate(r"/data/proj\special/sub/file5.txt", FileKind.DOC, 100, 10.0, 9, 1)
    for cand in (c5, c6, c7, c8, c9):
        db.insert_file(cand)

    assert [f.path for f in db.find_files_under("/data/proj_special")] == ["/data/proj_special/sub/file1.txt"]
    assert [f.path for f in db.find_files_under("/data/proj%special")] == ["/data/proj%special/sub/file3.txt"]
    assert [f.path for f in db.find_files_under(r"/data/proj\special")] == [r"/data/proj\special/sub/file5.txt"]
    assert len(db.find_files_under("/")) == 9


def test_fts_search_snippet_highlighting(db: Database) -> None:
    """Verify fts_search highlights matches both with SQLite snippets and explicit terms."""
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
    assert "«brown»" in results[0]["snippet"]
    assert "«fox»" in results[0]["snippet"]

    results_with_terms = db.fts_search('"brown" OR "fox"', terms=["brown", "fox"])
    assert len(results_with_terms) == 1
    assert "«brown»" in results_with_terms[0]["snippet"]


def test_fts_search_includes_loc_and_excerpt(db: Database) -> None:
    """Verify fts_search includes loc, excerpt, and negative BM25 rank."""
    candidate = Candidate("/tmp/manual.pdf", FileKind.PDF, 2048, 10.0, 1, 1)
    file_id = db.insert_file(candidate)

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
    assert hit["loc"] == 15.0
    assert hit["excerpt"] == "Page 15 content about system diagnostics"
    assert hit["rank"] < 0

    db.insert_segment(
        file_id=file_id,
        kind=SegmentKind.CHUNK,
        modality=Modality.TEXT,
        name="manual.pdf",
        body="Troubleshooting steps for system recovery",
    )

    results_default = db.fts_search("troubleshooting")
    assert len(results_default) == 1
    assert results_default[0]["loc"] == 0.0
    assert results_default[0]["excerpt"] == ""

    with db.transaction() as cursor:
        cursor.execute("INSERT INTO fts (rowid, name, body, original) VALUES (9999, 'orphan.pdf', 'system orphan', '')")

    results_orphan = db.fts_search("orphan")
    assert len(results_orphan) == 1
    assert results_orphan[0]["segment_id"] == 9999
    assert results_orphan[0]["file_id"] is None
    assert results_orphan[0]["loc"] == 0.0
    assert results_orphan[0]["excerpt"] == ""


def test_get_all_vectors_with_scale_and_file_segments(db: Database) -> None:
    """Verify get_all_vectors_with_scale marks only the first segment per (file, modality)."""
    c1 = Candidate("/tmp/multi.md", FileKind.DOC, 500, 10.0, 1, 1)
    fid1 = db.insert_file(c1)
    vec_bytes = np.ones(768, dtype=np.float16).tobytes()

    db.insert_segment(
        fid1,
        SegmentKind.CHUNK,
        Modality.TEXT,
        0.0,
        1.0,
        excerpt="chunk 0",
        vec=vec_bytes,
        body="chunk zero",
    )
    db.insert_segment(
        fid1,
        SegmentKind.CHUNK,
        Modality.TEXT,
        1.0,
        2.0,
        excerpt="chunk 1",
        vec=vec_bytes,
        body="chunk one",
    )

    seg_ids, _file_ids, _modalities, _matrix, sets_scale = db.get_all_vectors_with_scale()
    assert len(seg_ids) == 2
    assert sets_scale == [True, False]

    segs = db.get_file_segments(fid1)
    assert len(segs) == 2
    assert segs[0][1] == SegmentKind.CHUNK
    assert segs[0][5] == "chunk zero"

    db.update_file_kind(fid1, FileKind.SCREENSHOT)
    updated = db.find_by_id(fid1)
    assert updated is not None
    assert updated.kind == FileKind.SCREENSHOT


def test_fts_sanitization_and_wal_concurrent_readers_writers(db: Database) -> None:
    """Verify malformed FTS queries are sanitized and concurrent WAL readers/writers succeed."""
    assert db.fts_search('unclosed "quote') == []
    assert db.fts_search("***") == []
    assert db.fts_search('""') == []
    assert db.is_indexed("foo:bar") is False

    errors: list[Exception] = []
    vec_blob = np.ones(64, dtype=np.float16).tobytes()

    def writer(idx: int) -> None:
        try:
            cid = db.insert_file(Candidate(f"/tmp/f_{idx}.txt", FileKind.DOC, 10, float(idx), idx + 100, 1))
            db.insert_segment(
                cid,
                SegmentKind.CHUNK,
                Modality.TEXT,
                excerpt=f"body {idx}",
                vec=vec_blob,
                name=f"f_{idx}.txt",
                body=f"body {idx}",
            )
        except Exception as exc:  # noqa: BLE001 - thread worker captures any failure for main-thread assertion
            errors.append(exc)

    def reader() -> None:
        try:
            for _ in range(10):
                db.fts_search("body")
                db.get_vector_snapshot()
                db.stats()
        except Exception as exc:  # noqa: BLE001 - thread worker captures any failure for main-thread assertion
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(6)]
    threads.extend(threading.Thread(target=reader) for _ in range(4))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert db.stats()["files"] == 6
    assert db.get_vector_snapshot().matrix.shape == (6, 64)


def test_in_memory_database_and_shrink_memory() -> None:
    """Verify :memory: database uses write connection safely under lock and supports shrink_memory."""
    with Database(":memory:") as mem_db:
        cid = mem_db.insert_file(Candidate("/tmp/shrink.txt", FileKind.DOC, 42, 10.0, 500, 1))
        mem_db.insert_segment(
            cid,
            SegmentKind.CHUNK,
            Modality.TEXT,
            0.0,
            1.0,
            excerpt="memory test",
            vec=np.ones(768, dtype=np.float16).tobytes(),
            name="shrink.txt",
            body="memory test content",
        )

        errors: list[Exception] = []

        def worker() -> None:
            try:
                for _ in range(5):
                    mem_db.shrink_memory()
                    mem_db.stats()
                    mem_db.get_vector_snapshot()
            except Exception as exc:  # noqa: BLE001 - thread worker captures any failure for main-thread assertion
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        rec = mem_db.find_by_id(cid)
        assert rec is not None
        assert rec.path == "/tmp/shrink.txt"
        assert len(mem_db.fts_search("memory")) == 1
