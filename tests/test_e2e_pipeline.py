"""End-to-end indexing pipeline, database consistency, and real background watcher tests."""

import time
from pathlib import Path

import numpy as np

from semsearch.config import AppConfig
from semsearch.core.database import Database
from semsearch.core.ollama import OllamaClient
from semsearch.core.types import FileState
from semsearch.core.vectors import DIM, l2_normalize
from semsearch.services.indexer import IndexService
from semsearch.services.watcher import FileSystemWatcher
from tests.testbed import generate_testbed


class DeterministicOllama(OllamaClient):
    """Offline OllamaClient providing deterministic normalized float vectors."""

    def __init__(self, dim: int = DIM) -> None:
        super().__init__(base_url="http://127.0.0.1:11434")
        self.dim = dim

    def unload(self, model: str) -> None:
        assert model

    def embed(
        self,
        model: str,
        inputs: list[str | dict[str, str]],
        *,
        truncate: bool = True,
        num_ctx: int | None = None,
        keep_alive: int | str | None = None,
    ) -> list[list[float]]:
        del model, truncate, num_ctx, keep_alive
        results: list[list[float]] = []
        for inp in inputs:
            seed = sum(ord(c) for c in str(inp)[:64]) % 100000
            rng = np.random.default_rng(seed)
            vec = l2_normalize(rng.standard_normal(self.dim).astype(np.float32))
            results.append([float(x) for x in vec])
        return results


def test_testbed_indexing_full_pipeline_e2e(tmp_path: Path) -> None:
    testbed_dir = tmp_path / "testbed"
    generate_testbed(testbed_dir)

    with (
        Database(tmp_path / "semsearch.db") as db,
        Database(tmp_path / "code.db") as code_db,
        DeterministicOllama() as ollama,
    ):
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

        indexer = IndexService(db=db, code_db=code_db, ollama=ollama, config=config)
        indexer.crawl_and_sync(is_code=False)
        indexer.crawl_and_sync(is_code=True)

        gen_stats = db.stats()
        code_stats = code_db.stats()

        assert gen_stats["files"] == 10
        assert gen_stats["pending"] == 10
        assert code_stats["files"] == 2
        assert code_stats["pending"] == 2

        while True:
            rec, target_db, _ = indexer._get_next_file()
            if rec is None or target_db is None:
                break
            assert indexer.process_file(rec, target_db) is True

        gen_stats_done = db.stats()
        code_stats_done = code_db.stats()

        assert gen_stats_done["pending"] == 0
        assert gen_stats_done["failed"] == 0
        assert gen_stats_done["files"] == 10
        assert gen_stats_done["vectors"] > 0

        assert code_stats_done["pending"] == 0
        assert code_stats_done["failed"] == 0
        assert code_stats_done["files"] == 2
        assert code_stats_done["vectors"] > 0


def test_database_consistency_and_foreign_keys_e2e(tmp_path: Path) -> None:
    testbed_dir = tmp_path / "testbed"
    generate_testbed(testbed_dir)

    with (
        Database(tmp_path / "semsearch.db") as db,
        Database(tmp_path / "code.db") as code_db,
        DeterministicOllama() as ollama,
    ):
        config = AppConfig(
            folders=[str(testbed_dir / "images"), str(testbed_dir / "docs")],
            code_folders=[str(testbed_dir / "code")],
            code_enabled=True,
        )

        indexer = IndexService(db=db, code_db=code_db, ollama=ollama, config=config)
        indexer.crawl_and_sync(is_code=False)
        indexer.crawl_and_sync(is_code=True)

        while True:
            rec, target_db, _ = indexer._get_next_file()
            if rec is None or target_db is None:
                break
            indexer.process_file(rec, target_db)

        assert db._write_conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert code_db._write_conn.execute("PRAGMA foreign_key_check").fetchall() == []

        c = db._write_conn.cursor()
        orphan_query = "SELECT count(*) FROM segments WHERE file NOT IN (SELECT id FROM files)"
        orphan_segments = c.execute(orphan_query).fetchone()[0]
        assert orphan_segments == 0

        vector_segments = c.execute("SELECT count(*) FROM segments WHERE vec IS NOT NULL").fetchone()[0]
        assert vector_segments > 0
        assert vector_segments == db.stats()["vectors"]

        fts_count = c.execute("SELECT count(*) FROM fts").fetchone()[0]
        assert fts_count > 0
        assert db.stats()["failed"] == 0


def test_incremental_indexing_modification_and_deletion_e2e(tmp_path: Path) -> None:
    testbed_dir = tmp_path / "testbed"
    testbed = generate_testbed(testbed_dir)

    with Database(tmp_path / "semsearch.db") as db, DeterministicOllama() as ollama:
        config = AppConfig(
            folders=[str(testbed_dir / "docs"), str(testbed_dir / "images")],
            code_enabled=False,
        )

        indexer = IndexService(db=db, ollama=ollama, config=config)
        indexer.crawl_and_sync(is_code=False)

        while True:
            rec, target_db, _ = indexer._get_next_file()
            if rec is None or target_db is None:
                break
            indexer.process_file(rec, target_db)

        init_fts = db.fts_search("ERR-4096-FAIL")
        assert len(init_fts) == 1

        # 1. Modify notes.txt
        time.sleep(0.02)
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

        updated_fts = db.fts_search("SERIAL-DELTA-8899")
        assert len(updated_fts) == 1

        # 2. Delete photo_sunset.jpg
        photo_path = testbed["photo_sunset"]
        photo_path.unlink()

        indexer.crawl_and_sync(is_code=False)
        assert db.find_by_path(str(photo_path)) is None


def test_inotify_filesystem_watcher_e2e(tmp_path: Path) -> None:
    """Verify real background IndexService + FileSystemWatcher debounce and index create/modify end-to-end."""
    watch_dir = tmp_path / "watched_docs"
    watch_dir.mkdir()

    with Database(tmp_path / "watcher.db") as db, DeterministicOllama() as ollama:
        config = AppConfig(
            folders=[str(watch_dir)],
            code_enabled=False,
        )

        indexer = IndexService(db=db, ollama=ollama, config=config)
        watcher = FileSystemWatcher(index_service=indexer, config=config, check_interval_sec=0.05)

        indexer.start()
        watcher.start()
        try:
            time.sleep(0.1)

            # 1. Create a new file and wait for automatic debounce + background indexing (zero manual flushes)
            new_doc = watch_dir / "realtime_notes.txt"
            new_doc.write_text("Realtime event log: ALPHA-1122", encoding="utf-8")
            resolved_doc = str(new_doc.resolve())

            deadline = time.monotonic() + 6.0
            while time.monotonic() < deadline:
                rec = db.find_by_path(resolved_doc)
                if rec is not None and rec.state == FileState.DONE and len(db.fts_search("ALPHA-1122")) == 1:
                    break
                time.sleep(0.05)

            created_rec = db.find_by_path(resolved_doc)
            assert created_rec is not None
            assert created_rec.state == FileState.DONE
            assert len(db.fts_search("ALPHA-1122")) == 1
            assert db.stats()["vectors"] > 0

            # 2. Modify the watched file and confirm automatic debounce + re-indexing end-to-end
            time.sleep(0.05)
            new_doc.write_text("Realtime updated log: OMEGA-9988", encoding="utf-8")

            deadline = time.monotonic() + 6.0
            while time.monotonic() < deadline:
                rec = db.find_by_path(resolved_doc)
                if rec is not None and rec.state == FileState.DONE and len(db.fts_search("OMEGA-9988")) == 1:
                    break
                time.sleep(0.05)

            updated_rec = db.find_by_path(resolved_doc)
            assert updated_rec is not None
            assert updated_rec.state == FileState.DONE
            assert len(db.fts_search("OMEGA-9988")) == 1
            assert db.fts_search("ALPHA-1122") == []
            assert db.stats()["failed"] == 0
        finally:
            watcher.stop()
            indexer.stop()
