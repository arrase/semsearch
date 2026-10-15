"""Tests for IndexService, priority queue tiers, pipelining, and partial continuation."""

from pathlib import Path
import time
from unittest.mock import MagicMock
import numpy as np
import pytest

from semsearch.config import AppConfig
from semsearch.core.database import Database
from semsearch.core.ollama import OllamaClient
from semsearch.core.types import (
    Candidate,
    EngineActivity,
    ExtractedSegment,
    FileKind,
    FileState,
    Modality,
    SegmentKind,
)
from semsearch.core.vectors import DIM
from semsearch.services.indexer import IndexQueueTier, IndexService, _code_place



class MockOllama(OllamaClient):
    def embed(self, model: str, inputs: list[str | dict[str, str]], truncate: bool = True) -> list[list[float]]:
        return [[0.1] * DIM for _ in inputs]


def test_process_file_with_ollama_vectors(tmp_path: Path):
    doc_path = tmp_path / "notes.md"
    doc_path.write_text("# Chapter 1\nThis is a long document with interesting notes.", encoding="utf-8")

    db = Database(tmp_path / "index.sqlite")
    cand = Candidate(path=str(doc_path), kind=FileKind.DOC, size=len(doc_path.read_bytes()), mtime=1.0, inode=1, device=1)
    fid = db.insert_file(cand)

    service = IndexService(db=db, ollama=MockOllama())
    file_rec = db.find_by_path(str(doc_path))
    assert file_rec is not None

    success = service.process_file(file_rec, db)
    assert success is True

    updated_rec = db.find_by_path(str(doc_path))
    assert updated_rec.state == FileState.DONE

    seg_ids, file_ids, modalities, mat = db.get_all_vectors()
    assert len(seg_ids) >= 1
    assert fid in file_ids
    assert mat.shape[0] == len(seg_ids)
    assert mat.shape[1] == DIM


def test_multi_tier_priority_ordering(tmp_path: Path):
    db = Database(tmp_path / "index.sqlite")
    code_db = Database(tmp_path / "code.sqlite")
    config = AppConfig(code_enabled=True, code_folders=[str(tmp_path / "code")])

    # 1. Backfill general file
    p_gen = tmp_path / "general.md"
    p_gen.write_text("General doc", encoding="utf-8")
    fid_gen = db.insert_file(Candidate(str(p_gen), FileKind.DOC, 10, 1.0, 1, 1))

    # 2. Backfill code file
    p_code = tmp_path / "code" / "app.py"
    p_code.parent.mkdir(parents=True, exist_ok=True)
    p_code.write_text("def run(): pass", encoding="utf-8")
    fid_code = code_db.insert_file(Candidate(str(p_code), FileKind.CODE, 10, 1.0, 2, 1))

    # 3. Partial file
    p_partial = tmp_path / "partial.pdf"
    p_partial.write_text("fake pdf", encoding="utf-8")
    fid_part = db.insert_file(Candidate(str(p_partial), FileKind.PDF, 10, 1.0, 3, 1))
    db.update_file_state(fid_part, FileState.PARTIAL, info={"last_page": 30})

    # 4. Immediate new file
    p_new = tmp_path / "urgent.md"
    p_new.write_text("Urgent new file", encoding="utf-8")
    fid_new = db.insert_file(Candidate(str(p_new), FileKind.DOC, 10, 2.0, 4, 1))

    service = IndexService(db=db, code_db=code_db, config=config)
    service.enqueue_file(str(p_new), tier=IndexQueueTier.NEW_FILES)

    # 1st: Tier 1 (NEW_FILES)
    rec1, target_db1, tier1 = service._get_next_file()
    assert tier1 == IndexQueueTier.NEW_FILES
    assert rec1.id == fid_new
    db.update_file_state(fid_new, FileState.DONE)

    # 2nd: Tier 2 (BACKFILL general)
    rec2, target_db2, tier2 = service._get_next_file()
    assert tier2 == IndexQueueTier.BACKFILL
    assert rec2.id == fid_gen

    # Mark fid_gen as done so it's not pending anymore
    db.update_file_state(fid_gen, FileState.DONE)

    # 3rd: Tier 3 (CODE)
    rec3, target_db3, tier3 = service._get_next_file()
    assert tier3 == IndexQueueTier.CODE
    assert rec3.id == fid_code

    code_db.update_file_state(fid_code, FileState.DONE)

    # 4th: Tier 4 (LONG_FILES)
    rec4, target_db4, tier4 = service._get_next_file()
    assert tier4 == IndexQueueTier.LONG_FILES
    assert rec4.id == fid_part


def test_partial_continuation_flow(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "index.sqlite")

    pdf_file = tmp_path / "big_report.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 mock")

    cand = Candidate(str(pdf_file), FileKind.PDF, 1000, 1.0, 10, 1)
    fid = db.insert_file(cand)

    call_count = 0

    def mock_extract_pdf(path, start_page=0, max_pages=30):
        nonlocal call_count
        call_count += 1
        if start_page == 0:
            segs = [ExtractedSegment(SegmentKind.PAGE, Modality.TEXT, loc=float(i + 1), excerpt=f"Page {i+1}", payload=f"Page text {i+1}") for i in range(30)]
            return segs, False
        else:
            segs = [ExtractedSegment(SegmentKind.PAGE, Modality.TEXT, loc=float(i + 1), excerpt=f"Page {i+1}", payload=f"Page text {i+1}") for i in range(30, 50)]
            return segs, True

    monkeypatch.setattr("semsearch.services.indexer.extract_pdf", mock_extract_pdf)

    service = IndexService(db=db, ollama=MockOllama())

    # First pass
    rec = db.find_by_path(str(pdf_file))
    service.process_file(rec, db)

    rec_after_pass1 = db.find_by_path(str(pdf_file))
    assert rec_after_pass1.state == FileState.PARTIAL
    assert rec_after_pass1.info["last_page"] == 30

    # Second pass (continuation)
    service.process_file(rec_after_pass1, db)

    rec_after_pass2 = db.find_by_path(str(pdf_file))
    assert rec_after_pass2.state == FileState.DONE
    assert rec_after_pass2.info["last_page"] == 50


def test_service_lifecycle_pause_resume_status(tmp_path: Path):
    db = Database(tmp_path / "index.sqlite")
    service = IndexService(db=db)

    assert service.get_status().activity == EngineActivity.STARTING

    service.start()
    assert service._worker_thread is not None
    assert service._worker_thread.is_alive()

    service.pause(reason="AC power")
    status_paused = service.get_status()
    assert status_paused.paused is True
    assert status_paused.waiting_for == "AC power"
    assert status_paused.activity == EngineActivity.PAUSED

    service.resume()
    status_resumed = service.get_status()
    assert status_resumed.paused is False
    assert status_resumed.waiting_for == ""

    service.stop()
    assert service._worker_thread is None


def test_modified_file_requeuing(tmp_path: Path):
    doc_path = tmp_path / "modified.txt"
    doc_path.write_text("initial content", encoding="utf-8")

    db = Database(tmp_path / "index.sqlite")
    st = doc_path.stat()
    cand = Candidate(
        path=str(doc_path),
        kind=FileKind.DOC,
        size=st.st_size,
        mtime=st.st_mtime,
        inode=st.st_ino,
        device=st.st_dev,
    )
    fid = db.insert_file(cand)
    db.insert_segment(fid, SegmentKind.CHUNK, Modality.TEXT, 0, 0, "initial content", None, "modified.txt", "initial content", "")
    db.update_file_state(fid, FileState.DONE)

    service = IndexService(db=db)

    doc_path.write_text("updated new content that is longer", encoding="utf-8")
    service.enqueue_file(str(doc_path))

    rec = db.find_by_id(fid)
    assert rec is not None
    assert rec.state == FileState.PENDING
    assert (fid, False) in service._new_files_queue

    cursor = db._conn.cursor()
    cursor.execute("SELECT id FROM segments WHERE file = ? AND kind != 'name'", (fid,))
    assert len(cursor.fetchall()) == 0


def test_extraction_failure_handling(tmp_path: Path, monkeypatch):
    doc_path = tmp_path / "broken.md"
    doc_path.write_text("# Corrupt", encoding="utf-8")

    db = Database(tmp_path / "index.sqlite")
    st = doc_path.stat()
    cand = Candidate(
        path=str(doc_path),
        kind=FileKind.DOC,
        size=st.st_size,
        mtime=st.st_mtime,
        inode=st.st_ino,
        device=st.st_dev,
    )
    fid = db.insert_file(cand)

    def failing_extract(path):
        raise RuntimeError("Extraction failed due to parser crash")

    monkeypatch.setattr("semsearch.services.indexer.extract_doc", failing_extract)

    service = IndexService(db=db)
    rec = db.find_by_id(fid)
    assert rec is not None

    success = service.process_file(rec, db)
    assert success is False

    updated_rec = db.find_by_id(fid)
    assert updated_rec is not None
    assert updated_rec.state == FileState.FAILED
    assert "Extraction failed due to parser crash" in (updated_rec.detail or "")


def test_crawl_and_sync_resets_activity_to_idle(tmp_path: Path):
    doc_folder = tmp_path / "docs"
    doc_folder.mkdir()
    (doc_folder / "note.txt").write_text("hello world", encoding="utf-8")

    db = Database(tmp_path / "index.sqlite")
    config = AppConfig(folders=[str(doc_folder)])
    service = IndexService(db=db, config=config)

    assert service.get_status().activity == EngineActivity.STARTING
    service.crawl_and_sync(is_code=False)
    assert service.get_status().activity == EngineActivity.IDLE

    stats = db.stats()
    assert stats["files"] == 1
    assert stats["pending"] == 1

    # Also verify code crawling resets activity to IDLE and populates code_db
    code_folder = tmp_path / "code"
    code_folder.mkdir()
    (code_folder / "app.py").write_text("print(1)", encoding="utf-8")
    code_db = Database(tmp_path / "code.sqlite")
    service_with_code = IndexService(db=db, code_db=code_db, config=AppConfig(code_folders=[str(code_folder)], code_enabled=True))
    service_with_code.crawl_and_sync(is_code=True)
    assert service_with_code.get_status().activity == EngineActivity.IDLE
    assert code_db.stats()["files"] == 1


def test_crawl_and_sync_early_returns_when_code_db_none(tmp_path: Path, monkeypatch):
    """Verify early return when is_code is True but code_db is None."""
    db = Database(tmp_path / "index.sqlite")
    code_folder = tmp_path / "code"
    code_folder.mkdir()
    service = IndexService(db=db, code_db=None, config=AppConfig(code_folders=[str(code_folder)], code_enabled=True))
    mock_crawl = MagicMock()
    monkeypatch.setattr("semsearch.services.indexer.crawl_folder", mock_crawl)

    initial_activity = service.get_status().activity
    service.crawl_and_sync(is_code=True)
    service.crawl_and_sync(roots=[str(code_folder)], is_code=True)
    assert service.get_status().activity == initial_activity
    mock_crawl.assert_not_called()


def test_crawl_and_sync_early_returns_when_stopped(tmp_path: Path, monkeypatch):
    """Verify early return when service is already stopped."""
    db = Database(tmp_path / "index.sqlite")
    doc_folder = tmp_path / "docs"
    doc_folder.mkdir()
    service = IndexService(db=db, config=AppConfig(folders=[str(doc_folder)]))
    mock_crawl = MagicMock()
    monkeypatch.setattr("semsearch.services.indexer.crawl_folder", mock_crawl)

    service._stop_event.set()
    initial_activity = service.get_status().activity
    service.crawl_and_sync(is_code=False)
    assert service.get_status().activity == initial_activity
    mock_crawl.assert_not_called()


def test_crawl_and_sync_preserves_indexing_state(tmp_path: Path):
    """Verify crawl_and_sync does not overwrite INDEXING activity to SYNCING or IDLE."""
    doc_folder = tmp_path / "docs"
    doc_folder.mkdir()
    (doc_folder / "file.txt").write_text("content", encoding="utf-8")

    db = Database(tmp_path / "index.sqlite")
    service = IndexService(db=db, config=AppConfig(folders=[str(doc_folder)]))
    service.status.activity = EngineActivity.INDEXING
    service.crawl_and_sync(is_code=False)

    assert service.get_status().activity == EngineActivity.INDEXING
    assert db.stats()["files"] == 1


def test_crawl_and_sync_preserves_paused_state(tmp_path: Path):
    """Verify crawl_and_sync preserves PAUSED status and activity."""
    doc_folder = tmp_path / "docs"
    doc_folder.mkdir()
    (doc_folder / "file.txt").write_text("content", encoding="utf-8")

    db = Database(tmp_path / "index.sqlite")
    service = IndexService(db=db, config=AppConfig(folders=[str(doc_folder)]))
    service.pause("user request")
    assert service.get_status().paused is True
    assert service.get_status().activity == EngineActivity.PAUSED

    service.crawl_and_sync(is_code=False)
    assert service.get_status().paused is True
    assert service.get_status().activity == EngineActivity.PAUSED
    assert db.stats()["files"] == 1


def test_crawl_and_sync_stops_loop_on_stop_event(tmp_path: Path, monkeypatch):
    """Verify root crawling loop terminates early when stop_event is set."""
    folder1 = tmp_path / "f1"
    folder2 = tmp_path / "f2"
    folder1.mkdir()
    folder2.mkdir()
    (folder1 / "a.txt").write_text("a", encoding="utf-8")
    (folder2 / "b.txt").write_text("b", encoding="utf-8")

    db = Database(tmp_path / "index.sqlite")
    config = AppConfig(folders=[str(folder1), str(folder2)])
    service = IndexService(db=db, config=config)

    crawled_roots: list[str] = []

    def mock_crawl(root: str, is_code: bool = False):
        crawled_roots.append(root)
        service._stop_event.set()
        return []

    monkeypatch.setattr("semsearch.services.indexer.crawl_folder", mock_crawl)
    service.crawl_and_sync(is_code=False)
    assert len(crawled_roots) == 1


def test_get_next_file_code_db_id_collision(tmp_path: Path):
    """Reproduce ID collision between index.db and code.db in _new_files_queue."""
    db = Database(tmp_path / "index.sqlite")
    code_db = Database(tmp_path / "code.sqlite")
    code_folder = tmp_path / "code"
    code_folder.mkdir(parents=True, exist_ok=True)
    config = AppConfig(code_enabled=True, code_folders=[str(code_folder)])

    # Insert file in general db (gets fid=1)
    p_gen = tmp_path / "general.md"
    p_gen.write_text("General content", encoding="utf-8")
    st_gen = p_gen.stat()
    cand_gen = Candidate(
        path=str(p_gen),
        kind=FileKind.DOC,
        size=st_gen.st_size,
        mtime=st_gen.st_mtime,
        inode=st_gen.st_ino,
        device=st_gen.st_dev,
    )
    fid_gen = db.insert_file(cand_gen)

    # Insert file in code db (gets fid=1 as well)
    p_code = code_folder / "main.py"
    p_code.write_text("def hello(): pass", encoding="utf-8")
    st_code = p_code.stat()
    cand_code = Candidate(
        path=str(p_code),
        kind=FileKind.CODE,
        size=st_code.st_size,
        mtime=st_code.st_mtime,
        inode=st_code.st_ino,
        device=st_code.st_dev,
    )
    fid_code = code_db.insert_file(cand_code)

    assert fid_gen == 1
    assert fid_code == 1
    assert db.find_by_id(1).state == FileState.PENDING
    assert code_db.find_by_id(1).state == FileState.PENDING

    service = IndexService(db=db, code_db=code_db, config=config)

    # Enqueue code file first
    service.enqueue_file(str(p_code), tier=IndexQueueTier.NEW_FILES)

    # _get_next_file should retrieve from code_db, not db
    rec, target_db, tier = service._get_next_file()
    assert tier == IndexQueueTier.NEW_FILES
    assert target_db is code_db
    assert rec is not None
    assert rec.id == fid_code
    assert rec.path == str(p_code.resolve())
    assert rec.kind == FileKind.CODE

    # Now enqueue general file
    service.enqueue_file(str(p_gen), tier=IndexQueueTier.NEW_FILES)
    rec2, target_db2, tier2 = service._get_next_file()
    assert tier2 == IndexQueueTier.NEW_FILES
    assert target_db2 is db
    assert rec2 is not None
    assert rec2.id == fid_gen
    assert rec2.path == str(p_gen.resolve())


def test_get_next_file_code_db_skips_non_pending_without_affecting_general_db(tmp_path: Path):
    """Verify that a non-pending file in code_db does not match a pending file with same ID in db."""
    db = Database(tmp_path / "index.sqlite")
    code_db = Database(tmp_path / "code.sqlite")
    code_folder = tmp_path / "code"
    code_folder.mkdir(parents=True, exist_ok=True)
    config = AppConfig(code_enabled=True, code_folders=[str(code_folder)])

    p_gen = tmp_path / "doc.txt"
    p_gen.write_text("General doc", encoding="utf-8")
    st_gen = p_gen.stat()
    fid_gen = db.insert_file(Candidate(str(p_gen), FileKind.DOC, st_gen.st_size, st_gen.st_mtime, st_gen.st_ino, st_gen.st_dev))

    p_code = code_folder / "script.py"
    p_code.write_text("print('test')", encoding="utf-8")
    st_code = p_code.stat()
    fid_code = code_db.insert_file(Candidate(str(p_code), FileKind.CODE, st_code.st_size, st_code.st_mtime, st_code.st_ino, st_code.st_dev))

    assert fid_gen == 1
    assert fid_code == 1

    # Mark code file as DONE in code_db, while gen file remains PENDING in db
    code_db.update_file_state(fid_code, FileState.DONE)

    service = IndexService(db=db, code_db=code_db, config=config)
    # Manually append the code file item to simulate a stale queue entry
    service._new_files_queue.append((fid_code, True))
    # And append general file
    service._new_files_queue.append((fid_gen, False))

    rec, target_db, tier = service._get_next_file()
    # It must skip (1, True) because code_db record is DONE, and return (1, False) with target_db is db
    assert tier == IndexQueueTier.NEW_FILES
    assert target_db is db
    assert rec is not None
    assert rec.id == fid_gen
    assert rec.path == str(p_gen.resolve())


def test_get_next_file_code_file_skips_when_code_db_none_without_matching_general_db(tmp_path: Path):
    """Verify that a code queue item does not fall back to general db when code_db is None."""
    db = Database(tmp_path / "index.sqlite")
    p_gen = tmp_path / "general.md"
    p_gen.write_text("General content", encoding="utf-8")
    st_gen = p_gen.stat()
    fid_gen = db.insert_file(Candidate(str(p_gen), FileKind.DOC, st_gen.st_size, st_gen.st_mtime, st_gen.st_ino, st_gen.st_dev))
    assert fid_gen == 1

    service = IndexService(db=db, code_db=None)
    # Enqueue a code entry with fid=1, simulating an item enqueued for code
    service._new_files_queue.append((1, True))

    rec, target_db, tier = service._get_next_file()
    # It must NOT return the general db record for fid=1 under NEW_FILES
    # Since code_db is None, the item is skipped, falling back to Tier 2 (BACKFILL)
    assert tier == IndexQueueTier.BACKFILL
    assert target_db is db
    assert rec is not None
    assert rec.id == fid_gen


def test_enqueue_file_folder_prefix_containment(tmp_path: Path):
    """Verify that directory prefix string matching does not confuse sibling folders."""
    db = Database(tmp_path / "index.sqlite")
    code_db = Database(tmp_path / "code.sqlite")
    code_root = tmp_path / "code"
    code_root.mkdir()
    sibling_root = tmp_path / "code_notes"
    sibling_root.mkdir()

    config = AppConfig(code_enabled=True, code_folders=[str(code_root)])
    service = IndexService(db=db, code_db=code_db, config=config)

    sibling_file = sibling_root / "note.txt"
    sibling_file.write_text("Not code", encoding="utf-8")

    service.enqueue_file(str(sibling_file))

    # sibling_file should be enqueued into general db with is_code=False
    assert (1, False) in service._new_files_queue
    assert db.find_by_id(1) is not None
    assert code_db.find_by_id(1) is None


def test_process_file_ollama_embed_failure_marks_failed(tmp_path: Path):
    doc_path = tmp_path / "failing.md"
    doc_path.write_text("Some text for embedding", encoding="utf-8")

    db = Database(tmp_path / "index.sqlite")
    cand = Candidate(
        path=str(doc_path),
        kind=FileKind.DOC,
        size=len(doc_path.read_bytes()),
        mtime=1.0,
        inode=1,
        device=1,
    )
    fid = db.insert_file(cand)

    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.embed.side_effect = RuntimeError("Ollama connection failed")

    service = IndexService(db=db, ollama=mock_ollama)
    file_rec = db.find_by_path(str(doc_path))
    assert file_rec is not None

    success = service.process_file(file_rec, db)
    assert success is False

    updated_rec = db.find_by_id(fid)
    assert updated_rec is not None
    assert updated_rec.state == FileState.FAILED
    assert "Ollama connection failed" in updated_rec.detail

    content_segments = [
        s
        for s in db._conn.execute(
            "SELECT * FROM segments WHERE file = ? AND kind != 'name'", (fid,)
        ).fetchall()
    ]
    assert len(content_segments) == 0


def test_worker_loop_process_file_exception_marks_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    doc_path = tmp_path / "crash.md"
    doc_path.write_text("Content to process", encoding="utf-8")

    db = Database(tmp_path / "index.sqlite")
    cand = Candidate(
        path=str(doc_path),
        kind=FileKind.DOC,
        size=len(doc_path.read_bytes()),
        mtime=1.0,
        inode=1,
        device=1,
    )
    fid = db.insert_file(cand)

    service = IndexService(db=db)

    call_count = 0

    def failing_process_file(file_record, target_db):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("Unexpected extraction crash")

    service.process_file = failing_process_file
    monkeypatch.setattr(service._work_event, "wait", service._stop_event.set)

    file_rec = db.find_by_id(fid)
    assert file_rec is not None
    assert file_rec.state == FileState.PENDING

    service._worker_loop()

    assert call_count == 1
    assert service.status.activity == EngineActivity.IDLE
    assert service.status.pending == 0

    updated_rec = db.find_by_id(fid)
    assert updated_rec is not None
    assert updated_rec.state == FileState.FAILED
    assert "Unexpected extraction crash" in updated_rec.detail


def test_worker_loop_code_db_process_file_exception_marks_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    code_folder = tmp_path / "code"
    code_folder.mkdir()
    code_path = code_folder / "crash.py"
    code_path.write_text("def crash(): pass", encoding="utf-8")

    db = Database(tmp_path / "index.sqlite")
    code_db = Database(tmp_path / "code.sqlite")
    cand = Candidate(
        path=str(code_path),
        kind=FileKind.CODE,
        size=len(code_path.read_bytes()),
        mtime=1.0,
        inode=1,
        device=1,
    )
    fid = code_db.insert_file(cand)

    config = AppConfig(code_enabled=True, code_folders=[str(code_folder)])
    service = IndexService(db=db, code_db=code_db, config=config)

    call_count = 0

    def failing_process_file(file_record, target_db):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("Code processing failure")

    service.process_file = failing_process_file
    monkeypatch.setattr(service._work_event, "wait", service._stop_event.set)

    service._worker_loop()

    assert call_count == 1
    assert db.find_by_id(fid) is None
    updated_rec = code_db.find_by_id(fid)
    assert updated_rec is not None
    assert updated_rec.state == FileState.FAILED
    assert "Code processing failure" in updated_rec.detail


def test_code_place_nested_git_repo(tmp_path: Path):
    """Verify _code_place walks up to .git before parent code_folders and prefixes title with repo name."""
    develop = tmp_path / "Develop"
    repo_dir = develop / "semsearch"
    (repo_dir / ".git").mkdir(parents=True)
    file_path = repo_dir / "src" / "semsearch" / "services" / "searcher.py"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("class SearchService: pass", encoding="utf-8")

    repo, title = _code_place(str(file_path), code_folders=[str(develop)])
    assert repo == str(repo_dir.resolve())
    assert title == "src/semsearch/services/searcher.py"


def test_worker_loop_shrinks_memory_and_blocks_until_work_arrives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify _worker_loop shrinks memory on db and code_db when transitioning to IDLE and wakes on enqueue_file."""
    db = Database(tmp_path / "index.sqlite")
    code_db = Database(tmp_path / "code.sqlite")
    config = AppConfig(code_enabled=True, code_folders=[str(tmp_path / "code")])
    service = IndexService(db=db, code_db=code_db, config=config)

    db_shrink_calls = 0
    code_db_shrink_calls = 0
    orig_db_shrink = db.shrink_memory
    orig_code_shrink = code_db.shrink_memory

    def track_db_shrink() -> None:
        nonlocal db_shrink_calls
        db_shrink_calls += 1
        orig_db_shrink()

    def track_code_shrink() -> None:
        nonlocal code_db_shrink_calls
        code_db_shrink_calls += 1
        orig_code_shrink()

    monkeypatch.setattr(db, "shrink_memory", track_db_shrink)
    monkeypatch.setattr(code_db, "shrink_memory", track_code_shrink)

    service.start()
    deadline = time.time() + 2.0
    while service.get_status().activity != EngineActivity.IDLE and time.time() < deadline:
        time.sleep(0.01)

    assert service.get_status().activity == EngineActivity.IDLE
    assert db_shrink_calls == 1
    assert code_db_shrink_calls == 1
    assert not service._work_event.is_set()

    # Enqueue a new file while idle and verify immediate wakeup and processing
    doc_path = tmp_path / "wake.md"
    doc_path.write_text("Wake up the indexer", encoding="utf-8")
    service.enqueue_file(str(doc_path))

    deadline = time.time() + 2.0
    while db.find_by_path(str(doc_path.resolve())).state != FileState.DONE and time.time() < deadline:
        time.sleep(0.01)
    while service.get_status().activity != EngineActivity.IDLE and time.time() < deadline:
        time.sleep(0.01)

    assert db.find_by_path(str(doc_path.resolve())).state == FileState.DONE
    assert db_shrink_calls >= 2
    assert code_db_shrink_calls >= 2

    service.stop()
    assert service._worker_thread is None


def test_get_status_skips_sqlite_stats_when_idle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify get_status() returns cached counters without calling db.stats() while activity is IDLE."""
    db = Database(tmp_path / "index.sqlite")
    code_db = Database(tmp_path / "code.sqlite")
    doc_folder = tmp_path / "docs"
    doc_folder.mkdir()
    (doc_folder / "item.txt").write_text("hello", encoding="utf-8")

    service = IndexService(db=db, code_db=code_db, config=AppConfig(folders=[str(doc_folder)], code_enabled=True))
    service.crawl_and_sync(is_code=False)
    assert service.get_status().activity == EngineActivity.IDLE
    assert service.get_status().files == 1

    stats_calls = 0
    orig_stats = db.stats

    def counting_stats() -> dict[str, int]:
        nonlocal stats_calls
        stats_calls += 1
        return orig_stats()

    monkeypatch.setattr(db, "stats", counting_stats)

    # Repeated polling while IDLE must execute 0 SQLite stats() queries
    for _ in range(5):
        status = service.get_status()
        assert status.activity == EngineActivity.IDLE
        assert status.files == 1

    assert stats_calls == 0

    # When not IDLE, get_status() updates status counts
    service.status.activity = EngineActivity.INDEXING
    status_active = service.get_status()
    assert status_active.activity == EngineActivity.INDEXING
    assert stats_calls == 1

