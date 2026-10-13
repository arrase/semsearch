"""Tests for inotify filesystem watcher and debouncing logic."""

from pathlib import Path
from watchdog.events import (
    DirDeletedEvent,
    DirMovedEvent,
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


def test_directory_move_updates_indexed_files(tmp_path: Path):
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

    # 1. Normal directory move
    dir1 = tmp_path / "dir1"
    dir1.mkdir()
    f1 = dir1 / "doc1.md"
    f2 = dir1 / "nested" / "doc2.md"
    f2.parent.mkdir()
    f1.write_text("doc1", encoding="utf-8")
    f2.write_text("doc2", encoding="utf-8")

    old_f1_path = str(f1.resolve())
    old_f2_path = str(f2.resolve())
    fid1 = db.insert_file(Candidate(path=old_f1_path, kind=FileKind.DOC, size=4, mtime=1.0, inode=101, device=1))
    fid2 = db.insert_file(Candidate(path=old_f2_path, kind=FileKind.DOC, size=4, mtime=1.0, inode=102, device=1))

    dir2 = tmp_path / "dir2"
    dir1.rename(dir2)

    handler.on_moved(DirMovedEvent(str(dir1), str(dir2)))

    new_f1_path = str((dir2 / "doc1.md").resolve())
    new_f2_path = str((dir2 / "nested" / "doc2.md").resolve())

    # Files have their paths updated in SQLite
    assert db.find_by_path(old_f1_path) is None
    assert db.find_by_path(old_f2_path) is None
    rec1 = db.find_by_path(new_f1_path)
    rec2 = db.find_by_path(new_f2_path)
    assert rec1 is not None and rec1.id == fid1
    assert rec2 is not None and rec2.id == fid2

    # New paths are added to the debounce bucket
    assert new_f1_path in bucket.paths
    assert new_f2_path in bucket.paths

    # 2. Directory moved to ignored/filtered path
    ignored_dir = tmp_path / ".hidden_dir"
    dir2.rename(ignored_dir)

    handler.on_moved(DirMovedEvent(str(dir2), str(ignored_dir)))

    # Files moved into a filtered/ignored path are deleted from SQLite
    assert db.find_by_id(fid1) is None
    assert db.find_by_id(fid2) is None
    assert db.find_by_path(new_f1_path) is None
    assert db.find_by_path(new_f2_path) is None
    assert len(db.find_files_under(str(ignored_dir))) == 0
    assert str((ignored_dir / "doc1.md").resolve()) not in bucket.paths
    assert str((ignored_dir / "nested" / "doc2.md").resolve()) not in bucket.paths


def test_directory_move_empty_and_skipped_folders(tmp_path: Path):
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

    # 1. Moving an empty directory does not fail
    empty_src = tmp_path / "empty1"
    empty_dest = tmp_path / "empty2"
    empty_src.mkdir()
    empty_src.rename(empty_dest)
    handler.on_moved(DirMovedEvent(str(empty_src), str(empty_dest)))
    assert len(bucket.paths) == 0

    # 2. Moving directory to node_modules (SKIPPED_FOLDER_NAMES) deletes indexed files
    source_dir = tmp_path / "source_dir"
    source_dir.mkdir()
    f1 = source_dir / "index.md"
    f1.write_text("index", encoding="utf-8")
    fid = db.insert_file(Candidate(path=str(f1.resolve()), kind=FileKind.DOC, size=5, mtime=1.0, inode=201, device=1))

    dest_modules = tmp_path / "node_modules"
    source_dir.rename(dest_modules)
    handler.on_moved(DirMovedEvent(str(source_dir), str(dest_modules)))

    assert db.find_by_id(fid) is None
    assert len(db.find_files_under(str(dest_modules))) == 0


def test_directory_move_does_not_affect_sibling_with_like_wildcard(tmp_path: Path):
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

    # Create dir_a and dir-a (where '_' in SQL LIKE would match '-')
    dir_a = tmp_path / "dir_a"
    dir_dash_a = tmp_path / "dir-a"
    dir_a.mkdir()
    dir_dash_a.mkdir()

    file_a = dir_a / "doc.md"
    file_dash = dir_dash_a / "doc.md"
    file_a.write_text("a", encoding="utf-8")
    file_dash.write_text("dash", encoding="utf-8")

    fid_a = db.insert_file(Candidate(path=str(file_a.resolve()), kind=FileKind.DOC, size=1, mtime=1.0, inode=301, device=1))
    fid_dash = db.insert_file(Candidate(path=str(file_dash.resolve()), kind=FileKind.DOC, size=4, mtime=1.0, inode=302, device=1))

    # Move dir_a to dir_b
    dir_b = tmp_path / "dir_b"
    dir_a.rename(dir_b)
    handler.on_moved(DirMovedEvent(str(dir_a), str(dir_b)))

    # dir_a file moved
    assert db.find_by_path(str(file_a.resolve())) is None
    new_a = db.find_by_path(str((dir_b / "doc.md").resolve()))
    assert new_a is not None and new_a.id == fid_a

    # dir-a file completely unaffected
    dash_rec = db.find_by_path(str(file_dash.resolve()))
    assert dash_rec is not None and dash_rec.id == fid_dash


