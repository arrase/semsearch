"""Tests for inotify filesystem watcher and debouncing logic."""

from pathlib import Path
from watchdog.events import (
    DirDeletedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
)

from semsearch.config import AppConfig
from semsearch.core.database import Database
from semsearch.core.types import Candidate, FileKind
from semsearch.services.watcher import DebounceBucket, FileSystemWatcher, SemSearchEventHandler


class MockIndexService:
    def __init__(self, db: Database, code_db: Database | None = None):
        self.db = db
        self.code_db = code_db
        self.enqueued: list[str] = []

    def enqueue_file(self, path: str):
        self.enqueued.append(path)


def test_debounce_bucket_quiet_window():
    bucket = DebounceBucket(quiet_window=0.75)

    bucket.add("/path/to/doc.pdf", now=100.0)
    assert bucket.is_ready(now=100.5) is False
    assert bucket.is_ready(now=100.74) is False
    assert bucket.is_ready(now=100.75) is True

    # New event pushes quiet window
    bucket.add("/path/to/doc.pdf", now=100.6)
    assert bucket.is_ready(now=101.0) is False
    assert bucket.is_ready(now=101.35) is True

    paths = bucket.flush()
    assert paths == {"/path/to/doc.pdf"}
    assert bucket.is_ready(now=102.0) is False


def test_debounce_bucket_max_ceiling():
    # Ceiling 600s, quiet 60s
    bucket = DebounceBucket(quiet_window=60.0, max_ceiling=600.0)

    bucket.add("/repo/file.py", now=1000.0)
    # Continuous bursts every 30s
    for t in range(1030, 1600, 30):
        bucket.add("/repo/file.py", now=float(t))
        assert bucket.is_ready(now=float(t)) is False

    # At t = 1600.0 (1000.0 + 600.0), ceiling is reached even if quiet window is not
    assert bucket.is_ready(now=1600.0) is True


def test_event_handler_filtering_and_routing(tmp_path: Path):
    db = Database(tmp_path / "index.sqlite")
    bucket = DebounceBucket(quiet_window=0.75)
    config = AppConfig(excluded_types=["heic"])

    handler = SemSearchEventHandler(
        root=str(tmp_path),
        is_code=False,
        bucket=bucket,
        db=db,
        config=config,
    )

    # 1. Hidden file ignored
    handler.on_created(FileCreatedEvent(str(tmp_path / ".hidden.txt")))
    assert len(bucket.paths) == 0

    # 2. Skipped directory ignored
    handler.on_created(FileCreatedEvent(str(tmp_path / "node_modules" / "pkg" / "index.js")))
    assert len(bucket.paths) == 0

    # 3. Excluded type ignored
    handler.on_created(FileCreatedEvent(str(tmp_path / "photo.heic")))
    assert len(bucket.paths) == 0

    # 4. Valid file added
    valid_file = tmp_path / "document.pdf"
    valid_file.write_bytes(b"%PDF-1.4 test")
    handler.on_created(FileCreatedEvent(str(valid_file)))
    assert str(valid_file) in bucket.paths


def test_event_handler_move_and_delete(tmp_path: Path):
    db = Database(tmp_path / "index.sqlite")
    bucket = DebounceBucket(quiet_window=0.75)
    config = AppConfig()

    handler = SemSearchEventHandler(
        root=str(tmp_path),
        is_code=False,
        bucket=bucket,
        db=db,
        config=config,
    )

    old_file = tmp_path / "old.md"
    old_file.write_text("content", encoding="utf-8")
    cand = Candidate(path=str(old_file), kind=FileKind.DOC, size=7, mtime=1.0, inode=10, device=1)
    fid = db.insert_file(cand)

    # Move event
    new_file = tmp_path / "new.md"
    new_file.write_text("content", encoding="utf-8")
    handler.on_moved(FileMovedEvent(str(old_file), str(new_file)))

    assert db.find_by_path(str(old_file)) is None
    assert db.find_by_path(str(new_file)) is not None
    assert str(new_file) in bucket.paths

    # Delete event
    handler.on_deleted(FileDeletedEvent(str(new_file)))
    assert db.find_by_path(str(new_file)) is None


def test_watcher_start_stop(tmp_path: Path):
    db = Database(tmp_path / "index.sqlite")
    indexer = MockIndexService(db=db)
    watch_dir = tmp_path / "watched"
    watch_dir.mkdir()

    config = AppConfig(folders=[str(watch_dir)])
    watcher = FileSystemWatcher(indexer, config)

    watcher.start()
    assert watcher._observer is not None
    assert watcher._timer_thread is not None
    assert watcher._observer.is_alive()

    watcher.stop()
    assert watcher._observer is None
    assert watcher._timer_thread is None


def test_directory_deletion_cascades_indexed_files(tmp_path: Path):
    db = Database(tmp_path / "index.sqlite")
    bucket = DebounceBucket(quiet_window=0.75)
    config = AppConfig()

    handler = SemSearchEventHandler(
        root=str(tmp_path),
        is_code=False,
        bucket=bucket,
        db=db,
        config=config,
    )

    subfolder = tmp_path / "subfolder"
    subfolder.mkdir()
    f1 = subfolder / "doc1.txt"
    f2 = subfolder / "doc2.txt"
    f1.write_text("doc1", encoding="utf-8")
    f2.write_text("doc2", encoding="utf-8")

    fid1 = db.insert_file(Candidate(path=str(f1), kind=FileKind.DOC, size=4, mtime=1.0, inode=1, device=1))
    fid2 = db.insert_file(Candidate(path=str(f2), kind=FileKind.DOC, size=4, mtime=1.0, inode=2, device=1))

    assert db.find_by_id(fid1) is not None
    assert db.find_by_id(fid2) is not None

    handler.on_deleted(DirDeletedEvent(str(subfolder)))

    assert db.find_by_id(fid1) is None
    assert db.find_by_id(fid2) is None
    assert len(db.find_files_under(str(subfolder))) == 0
