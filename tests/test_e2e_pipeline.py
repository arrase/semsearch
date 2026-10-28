"""End-to-end indexing pipeline, database consistency, and incremental watcher tests."""

import time
from pathlib import Path

import numpy as np

from semsearch.config import AppConfig
from semsearch.core.database import Database
from semsearch.core.types import FileState
from semsearch.core.vectors import DIM, l2_normalize
from semsearch.services.indexer import IndexService
from semsearch.services.watcher import FileSystemWatcher
from tests.testbed import generate_testbed


class DeterministicOllama:
    """Mock OllamaClient providing deterministic normalized float vectors."""

    def embed(self, model: str, inputs: list[str | dict[str, str]]) -> list[list[float]]:
        results = []
        for inp in inputs:
            seed = sum(ord(c) for c in str(inp)[:64]) % 100000
            rng = np.random.default_rng(seed)
            vec = l2_normalize(rng.standard_normal(DIM).astype(np.float32))
            results.append(vec.tolist())
        return results


def test_testbed_indexing_full_pipeline_e2e(tmp_path: Path) -> None:
    testbed_dir = tmp_path / "testbed"
    generate_testbed(testbed_dir)

    db_path = tmp_path / "semsearch.db"
    code_db_path = tmp_path / "code.db"
    db = Database(db_path)
    code_db = Database(code_db_path)

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

    ollama = DeterministicOllama()
    indexer = IndexService(db=db, code_db=code_db, ollama=ollama, config=config)

    # Crawl and synchronize both general and code directories
    indexer.crawl_and_sync(is_code=False)
    indexer.crawl_and_sync(is_code=True)

    gen_stats = db.stats()
    code_stats = code_db.stats()

    assert gen_stats["files"] == 9
    assert gen_stats["pending"] == 9
    assert code_stats["files"] == 2
    assert code_stats["pending"] == 2

    # Process all pending files
    while True:
        rec, target_db, _ = indexer._get_next_file()
        if rec is None or target_db is None:
            break
        success = indexer.process_file(rec, target_db)
        assert success is True

    # Check that all files are DONE and vectors populated
    gen_stats_done = db.stats()
    code_stats_done = code_db.stats()

    assert gen_stats_done["pending"] == 0
    assert gen_stats_done["files"] == 9
    assert gen_stats_done["vectors"] > 0

    assert code_stats_done["pending"] == 0
    assert code_stats_done["files"] == 2
    assert code_stats_done["vectors"] > 0

    cursor = db._conn.cursor()
    cursor.execute("SELECT count(*) FROM files WHERE state = 'failed'")
    assert cursor.fetchone()[0] == 0

    code_cursor = code_db._conn.cursor()
    code_cursor.execute("SELECT count(*) FROM files WHERE state = 'failed'")
    assert code_cursor.fetchone()[0] == 0


def test_database_consistency_and_foreign_keys_e2e(tmp_path: Path) -> None:
    testbed_dir = tmp_path / "testbed"
    generate_testbed(testbed_dir)

    db_path = tmp_path / "semsearch.db"
    code_db_path = tmp_path / "code.db"
    db = Database(db_path)
    code_db = Database(code_db_path)

    config = AppConfig(
        folders=[str(testbed_dir / "images"), str(testbed_dir / "docs")],
        code_folders=[str(testbed_dir / "code")],
        code_enabled=True,
    )

    indexer = IndexService(db=db, code_db=code_db, ollama=DeterministicOllama(), config=config)
    indexer.crawl_and_sync(is_code=False)
    indexer.crawl_and_sync(is_code=True)

    while True:
        rec, target_db, _ = indexer._get_next_file()
        if rec is None or target_db is None:
            break
        indexer.process_file(rec, target_db)

    # 1. Foreign keys integrity pragma
    assert db._conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert code_db._conn.execute("PRAGMA foreign_key_check").fetchall() == []

    # 2. Referential counts: segments and vectors
    c = db._conn.cursor()
    orphan_segments = c.execute("SELECT count(*) FROM segments WHERE file NOT IN (SELECT id FROM files)").fetchone()[0]
    assert orphan_segments == 0

    vector_segments = c.execute("SELECT count(*) FROM segments WHERE vec IS NOT NULL").fetchone()[0]
    assert vector_segments > 0

    # 3. FTS5 index consistency
    fts_count = c.execute("SELECT count(*) FROM fts").fetchone()[0]
    assert fts_count > 0


def test_incremental_indexing_modification_and_deletion_e2e(tmp_path: Path) -> None:
    testbed_dir = tmp_path / "testbed"
    testbed = generate_testbed(testbed_dir)

    db_path = tmp_path / "semsearch.db"
    db = Database(db_path)

    config = AppConfig(
        folders=[str(testbed_dir / "docs"), str(testbed_dir / "images")],
        code_enabled=False,
    )

    indexer = IndexService(db=db, ollama=DeterministicOllama(), config=config)
    indexer.crawl_and_sync(is_code=False)

    while True:
        rec, target_db, _ = indexer._get_next_file()
        if rec is None or target_db is None:
            break
        indexer.process_file(rec, target_db)

    # Verify initial FTS search
    init_fts = db.fts_search("ERR-4096-FAIL")
    assert len(init_fts) == 1

    # 1. Modify notes.txt
    time.sleep(0.01)
    notes_path = testbed["notes"]
    notes_path.write_text(
        "Updated content with new tracking token SERIAL-DELTA-8899\nMaintenance complete.", encoding="utf-8"
    )

    indexer.crawl_and_sync(is_code=False)
    assert db.stats()["pending"] == 1

    file_rec = db.find_by_path(str(notes_path))
    assert file_rec is not None
    assert file_rec.state == FileState.PENDING
    indexer.process_file(file_rec, db)

    # Search for new token
    updated_fts = db.fts_search("SERIAL-DELTA-8899")
    assert len(updated_fts) == 1

    # 2. Delete photo_sunset.jpg
    photo_path = testbed["photo_sunset"]
    photo_path.unlink()

    indexer.crawl_and_sync(is_code=False)
    assert db.find_by_path(str(photo_path)) is None


def test_inotify_filesystem_watcher_e2e(tmp_path: Path) -> None:
    watch_dir = tmp_path / "watched_docs"
    watch_dir.mkdir()

    db_path = tmp_path / "watcher.db"
    db = Database(db_path)

    config = AppConfig(
        folders=[str(watch_dir)],
        code_enabled=False,
    )

    indexer = IndexService(db=db, ollama=DeterministicOllama(), config=config)
    watcher = FileSystemWatcher(index_service=indexer, config=config, check_interval_sec=0.05)
    watcher.start()

    # Create new file in watched directory
    new_doc = watch_dir / "realtime_notes.txt"
    new_doc.write_text("Realtime inotify event log: TOKEN-WATCH-1122", encoding="utf-8")

    # Manually trigger debounce bucket flush to simulate quiet window expiration quickly
    bucket = watcher._buckets.get(str(watch_dir.resolve()))
    assert bucket is not None

    deadline = time.time() + 2.0
    while time.time() < deadline:
        if str(new_doc.resolve()) in bucket.paths or db.find_by_path(str(new_doc.resolve())) is not None:
            break
        time.sleep(0.05)

    bucket.flush()
    indexer.enqueue_file(str(new_doc.resolve()))

    watcher.stop()

    rec = db.find_by_path(str(new_doc.resolve()))
    assert rec is not None
    indexer.process_file(rec, db)
    assert rec.state in (FileState.PENDING, FileState.DONE)
