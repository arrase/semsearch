"""Tests for IndexService, priority queue tiers, pipelining, and partial continuation."""

import time
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from semsearch.config import AppConfig
from semsearch.core.database import Database
from semsearch.core.ollama import (
    OllamaClient,
    OllamaConnectionError,
    OllamaModelError,
    OllamaTimeoutError,
)
from semsearch.core.rules import MAX_TEXT_FILE_BYTES
from semsearch.core.types import (
    Candidate,
    EngineActivity,
    ExtractedSegment,
    FileKind,
    FileState,
    Modality,
    SegmentKind,
)
from semsearch.core.vectors import DIM, float16_to_bytes
from semsearch.services.indexer import IndexQueueTier, IndexService, _code_place


class MockOllama(OllamaClient):
    def uses_gemma_prompts(self, model: str) -> bool:
        return "embeddinggemma" in model.lower()

    def embed(
        self,
        _model: str,
        inputs: list[str | dict[str, str]],
        *,
        truncate: bool = True,
        num_ctx: int | None = None,
        keep_alive: int | str | None = None,
    ) -> list[list[float]]:
        del truncate, num_ctx, keep_alive
        return [[0.1] * DIM for _ in inputs]


def test_process_file_with_ollama_vectors(tmp_path: Path) -> None:
    doc_path = tmp_path / "notes.md"
    doc_path.write_text("# Chapter 1\nThis is a long document with interesting notes.", encoding="utf-8")

    db = Database(tmp_path / "index.sqlite")
    cand = Candidate(
        path=str(doc_path), kind=FileKind.DOC, size=len(doc_path.read_bytes()), mtime=1.0, inode=1, device=1
    )
    fid = db.insert_file(cand)

    service = IndexService(db=db, ollama=MockOllama())
    file_rec = db.find_by_path(str(doc_path))
    assert file_rec is not None

    success = service.process_file(file_rec, db)
    assert success is True

    updated_rec = db.find_by_path(str(doc_path))
    assert updated_rec is not None
    assert updated_rec.state == FileState.DONE

    snap = db.get_vector_snapshot()
    assert len(snap.segment_ids) >= 1
    assert fid in snap.file_ids.tolist()
    assert snap.matrix.shape[0] == len(snap.segment_ids)
    assert snap.matrix.shape[1] == DIM


def test_multi_tier_priority_ordering(tmp_path: Path) -> None:
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
    rec1, _target_db1, tier1 = service._get_next_file()
    assert tier1 == IndexQueueTier.NEW_FILES
    assert rec1 is not None
    assert rec1.id == fid_new
    db.update_file_state(fid_new, FileState.DONE)

    # 2nd: Tier 2 (BACKFILL general)
    rec2, _target_db2, tier2 = service._get_next_file()
    assert tier2 == IndexQueueTier.BACKFILL
    assert rec2 is not None
    assert rec2.id == fid_gen

    # Mark fid_gen as done so it's not pending anymore
    db.update_file_state(fid_gen, FileState.DONE)

    # 3rd: Tier 3 (CODE)
    rec3, _target_db3, tier3 = service._get_next_file()
    assert tier3 == IndexQueueTier.CODE
    assert rec3 is not None
    assert rec3.id == fid_code

    code_db.update_file_state(fid_code, FileState.DONE)

    # 4th: Tier 4 (LONG_FILES)
    rec4, _target_db4, tier4 = service._get_next_file()
    assert tier4 == IndexQueueTier.LONG_FILES
    assert rec4 is not None
    assert rec4.id == fid_part


def test_partial_continuation_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = Database(tmp_path / "index.sqlite")

    pdf_file = tmp_path / "big_report.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 mock")

    cand = Candidate(str(pdf_file), FileKind.PDF, 1000, 1.0, 10, 1)
    db.insert_file(cand)

    call_count = 0

    def mock_extract_pdf(_path: str, start_page: int = 0, max_pages: int = 30) -> tuple[list[ExtractedSegment], bool]:
        del max_pages
        nonlocal call_count
        call_count += 1
        if start_page == 0:
            segs = [
                ExtractedSegment(
                    SegmentKind.PAGE,
                    Modality.TEXT,
                    loc=float(i + 1),
                    excerpt=f"Page {i + 1}",
                    payload=f"Page text {i + 1}",
                )
                for i in range(30)
            ]
            return segs, False
        segs = [
            ExtractedSegment(
                SegmentKind.PAGE,
                Modality.TEXT,
                loc=float(i + 1),
                excerpt=f"Page {i + 1}",
                payload=f"Page text {i + 1}",
            )
            for i in range(30, 50)
        ]
        return segs, True

    monkeypatch.setattr("semsearch.services.indexer.extract_pdf", mock_extract_pdf)

    service = IndexService(db=db, ollama=MockOllama())

    # First pass
    rec = db.find_by_path(str(pdf_file))
    assert rec is not None
    service.process_file(rec, db)

    rec_after_pass1 = db.find_by_path(str(pdf_file))
    assert rec_after_pass1 is not None
    assert rec_after_pass1.state == FileState.PARTIAL
    assert rec_after_pass1.info["last_page"] == 30

    # Second pass (continuation)
    service.process_file(rec_after_pass1, db)

    rec_after_pass2 = db.find_by_path(str(pdf_file))
    assert rec_after_pass2 is not None
    assert rec_after_pass2.state == FileState.DONE
    assert rec_after_pass2.info["last_page"] == 50


def test_service_lifecycle_pause_resume_status(tmp_path: Path) -> None:
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


def test_modified_file_requeuing(tmp_path: Path) -> None:
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
    db.insert_segment(
        fid,
        SegmentKind.CHUNK,
        Modality.TEXT,
        0,
        0,
        excerpt="initial content",
        vec=None,
        name="modified.txt",
        body="initial content",
        original="",
    )
    db.update_file_state(fid, FileState.DONE)

    service = IndexService(db=db)

    doc_path.write_text("updated new content that is longer", encoding="utf-8")
    service.enqueue_file(str(doc_path))

    rec = db.find_by_id(fid)
    assert rec is not None
    assert rec.state == FileState.PENDING
    assert (fid, False) in service._new_files_queue

    cursor = db._write_conn.cursor()
    cursor.execute("SELECT id FROM segments WHERE file = ? AND kind != 'name'", (fid,))
    assert len(cursor.fetchall()) == 0


def test_extraction_failure_handling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def failing_extract(_path: str) -> list[ExtractedSegment]:
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


def test_crawl_and_sync_resets_activity_to_idle(tmp_path: Path) -> None:
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
    service_with_code = IndexService(
        db=db, code_db=code_db, config=AppConfig(code_folders=[str(code_folder)], code_enabled=True)
    )
    service_with_code.crawl_and_sync(is_code=True)
    assert service_with_code.get_status().activity == EngineActivity.IDLE
    assert code_db.stats()["files"] == 1


def test_crawl_and_sync_early_returns_when_code_db_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_crawl_and_sync_early_returns_when_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_crawl_and_sync_preserves_indexing_state(tmp_path: Path) -> None:
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


def test_crawl_and_sync_preserves_paused_state(tmp_path: Path) -> None:
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


def test_crawl_and_sync_stops_loop_on_stop_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def mock_crawl(
        root: str,
        *,
        is_code: bool = False,
        excluded_folders: object = None,
        excluded_types: object = None,
        follow_symlinks: bool = False,
        skip_code_projects: bool = True,
        skip_datasets: bool = True,
    ) -> list[Candidate]:
        del is_code, excluded_folders, excluded_types, follow_symlinks, skip_code_projects, skip_datasets
        crawled_roots.append(root)
        service._stop_event.set()
        return []

    monkeypatch.setattr("semsearch.services.indexer.crawl_folder", mock_crawl)
    service.crawl_and_sync(is_code=False)
    assert len(crawled_roots) == 1


def test_get_next_file_code_db_id_collision(tmp_path: Path) -> None:
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
    rec_gen = db.find_by_id(1)
    rec_code = code_db.find_by_id(1)
    assert rec_gen is not None
    assert rec_code is not None
    assert rec_gen.state == FileState.PENDING
    assert rec_code.state == FileState.PENDING

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


def test_get_next_file_code_db_skips_non_pending_without_affecting_general_db(tmp_path: Path) -> None:
    """Verify that a non-pending file in code_db does not match a pending file with same ID in db."""
    db = Database(tmp_path / "index.sqlite")
    code_db = Database(tmp_path / "code.sqlite")
    code_folder = tmp_path / "code"
    code_folder.mkdir(parents=True, exist_ok=True)
    config = AppConfig(code_enabled=True, code_folders=[str(code_folder)])

    p_gen = tmp_path / "doc.txt"
    p_gen.write_text("General doc", encoding="utf-8")
    st_gen = p_gen.stat()
    fid_gen = db.insert_file(
        Candidate(str(p_gen), FileKind.DOC, st_gen.st_size, st_gen.st_mtime, st_gen.st_ino, st_gen.st_dev)
    )

    p_code = code_folder / "script.py"
    p_code.write_text("print('test')", encoding="utf-8")
    st_code = p_code.stat()
    fid_code = code_db.insert_file(
        Candidate(str(p_code), FileKind.CODE, st_code.st_size, st_code.st_mtime, st_code.st_ino, st_code.st_dev)
    )

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


def test_get_next_file_code_file_skips_when_code_db_none_without_matching_general_db(tmp_path: Path) -> None:
    """Verify that a code queue item does not fall back to general db when code_db is None."""
    db = Database(tmp_path / "index.sqlite")
    p_gen = tmp_path / "general.md"
    p_gen.write_text("General content", encoding="utf-8")
    st_gen = p_gen.stat()
    fid_gen = db.insert_file(
        Candidate(str(p_gen), FileKind.DOC, st_gen.st_size, st_gen.st_mtime, st_gen.st_ino, st_gen.st_dev)
    )
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


def test_enqueue_file_folder_prefix_containment(tmp_path: Path) -> None:
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


def test_process_file_ollama_embed_failure_marks_failed(tmp_path: Path) -> None:
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

    content_segments = list(
        db._write_conn.execute("SELECT * FROM segments WHERE file = ? AND kind != 'name'", (fid,)).fetchall()
    )
    assert len(content_segments) == 0


def test_worker_loop_process_file_exception_marks_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def failing_process_file(_file_record: object, _target_db: Database) -> bool:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("Unexpected extraction crash")

    monkeypatch.setattr(service, "process_file", failing_process_file)
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
) -> None:
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

    def failing_process_file(_file_record: object, _target_db: Database) -> bool:
        nonlocal call_count
        call_count += 1
        raise RuntimeError("Code processing failure")

    monkeypatch.setattr(service, "process_file", failing_process_file)
    monkeypatch.setattr(service._work_event, "wait", service._stop_event.set)

    service._worker_loop()

    assert call_count == 1
    assert db.find_by_id(fid) is None
    updated_rec = code_db.find_by_id(fid)
    assert updated_rec is not None
    assert updated_rec.state == FileState.FAILED
    assert "Code processing failure" in updated_rec.detail


def test_code_place_nested_git_repo(tmp_path: Path) -> None:
    """Verify _code_place walks up to .git before parent code_folders and prefixes title with repo name."""
    develop = tmp_path / "Develop"
    repo_dir = develop / "semsearch"
    (repo_dir / ".git").mkdir(parents=True)
    file_path = repo_dir / "src" / "semsearch" / "services" / "searcher.py"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("class SearchService: pass", encoding="utf-8")

    repo, title = _code_place(str(file_path), code_folders=[str(develop)])
    assert repo == str(repo_dir.resolve())
    assert title == "semsearch/src/semsearch/services/searcher.py"


def test_worker_loop_shrinks_memory_and_blocks_until_work_arrives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    while (
        (wake_rec := db.find_by_path(str(doc_path.resolve()))) is None or wake_rec.state != FileState.DONE
    ) and time.time() < deadline:
        time.sleep(0.01)
    while service.get_status().activity != EngineActivity.IDLE and time.time() < deadline:
        time.sleep(0.01)

    done_rec = db.find_by_path(str(doc_path.resolve()))
    assert done_rec is not None
    assert done_rec.state == FileState.DONE
    assert db_shrink_calls >= 2
    assert code_db_shrink_calls >= 2

    service.stop()
    assert service._worker_thread is None


def test_get_status_skips_sqlite_stats_when_idle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_ollama_transient_errors_preserve_pending_and_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify OllamaConnectionError/OllamaTimeoutError do not mark FAILED and retry with exponential backoff."""
    doc_path = tmp_path / "resilient.md"
    doc_path.write_text("Important text waiting for Ollama daemon recovery.", encoding="utf-8")

    db = Database(tmp_path / "index.sqlite")
    st = doc_path.stat()
    fid = db.insert_file(Candidate(str(doc_path), FileKind.DOC, st.st_size, st.st_mtime, st.st_ino, st.st_dev))

    attempts = 0

    class FlakyOllama(OllamaClient):
        def embed(
            self,
            _model: str,
            inputs: list[str | dict[str, str]],
            *,
            truncate: bool = True,
            num_ctx: int | None = None,
            keep_alive: int | str | None = None,
        ) -> list[list[float]]:
            del truncate, num_ctx, keep_alive
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OllamaConnectionError("daemon offline")
            if attempts == 2:
                raise OllamaTimeoutError("timeout")
            return [[0.25] * DIM for _ in inputs]

        def unload(self, _model: str) -> None:
            return None

    service = IndexService(db=db, ollama=FlakyOllama())
    waits: list[float | None] = []
    seen_waiting_states: list[tuple[EngineActivity, str, FileState]] = []

    def fake_stop_wait(timeout: float | None = None) -> bool:
        waits.append(timeout)
        rec = db.find_by_id(fid)
        assert rec is not None
        seen_waiting_states.append((service.status.activity, service.status.waiting_for, rec.state))
        return False

    monkeypatch.setattr(service._stop_event, "wait", fake_stop_wait)
    monkeypatch.setattr(service._work_event, "wait", service._stop_event.set)

    service._worker_loop()

    assert attempts == 3
    assert waits == [2.0, 4.0]
    assert seen_waiting_states == [
        (EngineActivity.PAUSED, "Ollama", FileState.PENDING),
        (EngineActivity.PAUSED, "Ollama", FileState.PENDING),
    ]
    final_rec = db.find_by_id(fid)
    assert final_rec is not None
    assert final_rec.state == FileState.DONE
    assert service.status.waiting_for == ""


def test_ollama_model_error_marks_failed(tmp_path: Path) -> None:
    """Verify OllamaModelError marks the file as FAILED rather than retrying indefinitely."""
    doc_path = tmp_path / "bad_model.md"
    doc_path.write_text("Text that triggers missing model error.", encoding="utf-8")

    db = Database(tmp_path / "index.sqlite")
    st = doc_path.stat()
    fid = db.insert_file(Candidate(str(doc_path), FileKind.DOC, st.st_size, st.st_mtime, st.st_ino, st.st_dev))

    class BadModelOllama(OllamaClient):
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
            raise OllamaModelError("model not found")

    service = IndexService(db=db, ollama=BadModelOllama())
    rec = db.find_by_id(fid)
    assert rec is not None
    assert service.process_file(rec, db) is False
    updated = db.find_by_id(fid)
    assert updated is not None
    assert updated.state == FileState.FAILED


def test_upsert_candidate_preserves_done_file_with_same_mtime_and_size(tmp_path: Path) -> None:
    """Verify unchanged DONE files are not requeued and keep their vectors."""
    doc_path = tmp_path / "stable.md"
    doc_path.write_text("Stable content with existing vectors", encoding="utf-8")

    db = Database(tmp_path / "index.sqlite")
    st = doc_path.stat()
    cand = Candidate(str(doc_path.resolve()), FileKind.DOC, st.st_size, st.st_mtime, st.st_ino, st.st_dev)
    fid = db.insert_file(cand)
    vec = float16_to_bytes(np.ones(DIM, dtype=np.float32))
    db.insert_segment(fid, SegmentKind.CHUNK, Modality.TEXT, excerpt="Stable", vec=vec, body="Stable content")
    db.update_file_state(fid, FileState.DONE)

    rec = db.find_by_id(fid)
    assert rec is not None
    returned_id = IndexService._upsert_candidate(db, rec, cand)
    assert returned_id == fid

    service = IndexService(db=db)
    service.enqueue_file(str(doc_path))

    after = db.find_by_id(fid)
    assert after is not None
    assert after.state == FileState.DONE
    assert len(service._new_files_queue) == 0
    assert len(db.get_vector_snapshot().segment_ids) == 1


def test_enqueue_file_blocks_secrets_skipped_dirs_and_oversized_text(tmp_path: Path) -> None:
    """Verify enqueue_file enforces unified should_skip rules and MAX_TEXT_FILE_BYTES."""
    db = Database(tmp_path / "index.sqlite")
    service = IndexService(db=db, config=AppConfig(excluded_folders=["private_dir"]))

    env_file = tmp_path / ".env"
    env_file.write_text("SECRET_KEY=123", encoding="utf-8")
    secret_txt = tmp_path / "prod_secret_notes.txt"
    secret_txt.write_text("password=abc", encoding="utf-8")
    node_mod = tmp_path / "node_modules" / "pkg" / "README.md"
    node_mod.parent.mkdir(parents=True)
    node_mod.write_text("# Package", encoding="utf-8")
    excl_file = tmp_path / "private_dir" / "note.txt"
    excl_file.parent.mkdir()
    excl_file.write_text("hidden", encoding="utf-8")
    huge_file = tmp_path / "giant.txt"
    with huge_file.open("wb") as fh:
        fh.truncate(MAX_TEXT_FILE_BYTES + 1)

    for blocked in (env_file, secret_txt, node_mod, excl_file, huge_file):
        service.enqueue_file(str(blocked))

    assert db.stats()["files"] == 0
    assert len(service._new_files_queue) == 0


def test_crawl_and_sync_excludes_default_heic_and_purges_removed_roots(tmp_path: Path) -> None:
    """Verify default excluded_types=['heic'] is honored and removed root folders are purged."""
    root1 = tmp_path / "root1"
    root2 = tmp_path / "root2"
    root1.mkdir()
    root2.mkdir()
    (root1 / "photo.heic").write_bytes(b"fake heic")
    (root1 / "photo.jpg").write_bytes(b"fake jpg")
    (root2 / "note.txt").write_text("root2 note", encoding="utf-8")

    db = Database(tmp_path / "index.sqlite")
    config = AppConfig(folders=[str(root1), str(root2)])
    assert config.excluded_types == ["heic"]
    service = IndexService(db=db, config=config)

    service.crawl_and_sync(is_code=False)
    paths = {rec.path for rec in db.find_all_files()}
    assert str((root1 / "photo.jpg").resolve()) in paths
    assert str((root2 / "note.txt").resolve()) in paths
    assert str((root1 / "photo.heic").resolve()) not in paths

    # Remove root2 from configured folders and sync again -> orphan file is purged
    config.folders = [str(root1)]
    service.crawl_and_sync(is_code=False)
    paths_after = {rec.path for rec in db.find_all_files()}
    assert paths_after == {str((root1 / "photo.jpg").resolve())}


def test_embed_segments_skips_empty_inputs_and_records_dim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify empty/Modality.NONE/no-word segments are never sent to Ollama and embedding_dim is persisted."""
    doc_path = tmp_path / "mixed.md"
    doc_path.write_text("placeholder", encoding="utf-8")

    db = Database(tmp_path / "index.sqlite")
    st = doc_path.stat()
    fid = db.insert_file(Candidate(str(doc_path), FileKind.DOC, st.st_size, st.st_mtime, st.st_ino, st.st_dev))

    segments = [
        ExtractedSegment(SegmentKind.CHUNK, Modality.NONE, excerpt="meta", payload=""),
        ExtractedSegment(SegmentKind.CHUNK, Modality.TEXT, excerpt="nums", payload="12345 67890 11111"),
        ExtractedSegment(SegmentKind.CHUNK, Modality.TEXT, excerpt="real", payload="Semantic search engine", focus=7.0),
    ]
    monkeypatch.setattr("semsearch.services.indexer.extract_doc", lambda _p: segments)

    seen_inputs: list[list[str | dict[str, str]]] = []

    class SpyOllama(OllamaClient):
        def uses_gemma_prompts(self, model: str) -> bool:
            return "embeddinggemma" in model.lower()

        def embed(
            self,
            _model: str,
            inputs: list[str | dict[str, str]],
            *,
            truncate: bool = True,
            num_ctx: int | None = None,
            keep_alive: int | str | None = None,
        ) -> list[list[float]]:
            del truncate, num_ctx, keep_alive
            seen_inputs.append(list(inputs))
            return [[0.5] * 512 for _ in inputs]

    service = IndexService(db=db, ollama=SpyOllama(), config=AppConfig(model="custom-embed"))
    rec = db.find_by_id(fid)
    assert rec is not None
    assert service.process_file(rec, db) is True

    assert len(seen_inputs) == 1
    assert len(seen_inputs[0]) == 1
    assert all(item != "" for item in seen_inputs[0])
    assert db.get_meta("embedding_dim") == "512"

    snap = db.get_vector_snapshot()
    assert len(snap.segment_ids) == 1
    assert snap.matrix.shape == (1, 512)
    seg_meta = db.get_segments_by_ids(snap.segment_ids.tolist())[int(snap.segment_ids[0])]
    assert seg_meta.focus == 7.0


def test_extract_segments_stores_media_duration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify probe_media_duration result is stored in FileRecord.info['duration'] for audio and video."""
    audio_path = tmp_path / "podcast.mp3"
    audio_path.write_bytes(b"ID3")
    db = Database(tmp_path / "index.sqlite")
    st = audio_path.stat()
    fid = db.insert_file(Candidate(str(audio_path), FileKind.AUDIO, st.st_size, st.st_mtime, st.st_ino, st.st_dev))

    monkeypatch.setattr("semsearch.services.indexer.probe_media_duration", lambda _p: 185.25)
    monkeypatch.setattr("semsearch.services.indexer.extract_audio", lambda _p, **_kw: [])

    service = IndexService(db=db)
    rec = db.find_by_id(fid)
    assert rec is not None
    assert service.process_file(rec, db) is True

    updated = db.find_by_id(fid)
    assert updated is not None
    assert updated.info["duration"] == 185.25


def test_idle_unload_and_reset_all_for_reindex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify idle VRAM unload, work listeners, and reset_all_for_reindex across general and code DBs."""
    db = Database(tmp_path / "index.sqlite")
    code_db = Database(tmp_path / "code.sqlite")
    doc_path = tmp_path / "doc.md"
    doc_path.write_text("Document text for embedding", encoding="utf-8")
    code_path = tmp_path / "app.py"
    code_path.write_text("def run():\n    return 1\n", encoding="utf-8")

    st_doc = doc_path.stat()
    st_code = code_path.stat()
    fid_doc = db.insert_file(
        Candidate(str(doc_path), FileKind.DOC, st_doc.st_size, st_doc.st_mtime, st_doc.st_ino, st_doc.st_dev)
    )
    fid_code = code_db.insert_file(
        Candidate(str(code_path), FileKind.CODE, st_code.st_size, st_code.st_mtime, st_code.st_ino, st_code.st_dev)
    )

    unloaded_models: list[str] = []

    class TrackingOllama(MockOllama):
        def unload(self, model: str) -> None:
            unloaded_models.append(model)

    config = AppConfig(model="model-v1", code_enabled=True, code_folders=[str(tmp_path)])
    service = IndexService(db=db, code_db=code_db, ollama=TrackingOllama(), config=config)
    work_notifications = 0

    def on_work() -> None:
        nonlocal work_notifications
        work_notifications += 1

    service.add_work_listener(on_work)

    rec_doc = db.find_by_id(fid_doc)
    rec_code = code_db.find_by_id(fid_code)
    assert rec_doc is not None
    assert rec_code is not None
    assert service.process_file(rec_doc, db) is True
    assert service.process_file(rec_code, code_db) is True

    # Idle transition after embedding triggers clean VRAM unload once
    monkeypatch.setattr(service._work_event, "wait", lambda: True)
    service._handle_idle_transition()
    service._handle_idle_transition()
    assert unloaded_models == ["model-v1"]

    # Manual reset_all_for_reindex resets both databases and notifies listeners
    assert service.reset_all_for_reindex() == 2
    assert work_notifications >= 1
    assert service._work_event.is_set()
    doc_after_reset = db.find_by_id(fid_doc)
    code_after_reset = code_db.find_by_id(fid_code)
    assert doc_after_reset is not None
    assert code_after_reset is not None
    assert doc_after_reset.state == FileState.PENDING
    assert code_after_reset.state == FileState.PENDING


def test_sync_model_state_resets_index_on_model_change(tmp_path: Path) -> None:
    """Verify sync_model_state records model metadata and resets indexed files when model changes."""
    db = Database(tmp_path / "index.sqlite")
    code_db = Database(tmp_path / "code.sqlite")
    fid_doc = db.insert_file(Candidate(str(tmp_path / "doc.md"), FileKind.DOC, 10, 1.0, 1, 1))
    db.update_file_state(fid_doc, FileState.DONE)

    config = AppConfig(model="model-v1", code_enabled=True, code_folders=[str(tmp_path)])
    service = IndexService(db=db, code_db=code_db, config=config)
    service.sync_model_state()
    assert db.get_meta("embedding_model") == "model-v1"
    assert code_db.get_meta("embedding_model") == "model-v1"

    config.model = "model-v2"
    service.sync_model_state()
    assert db.get_meta("embedding_model") == "model-v2"
    assert code_db.get_meta("embedding_model") == "model-v2"
    doc_after_model = db.find_by_id(fid_doc)
    assert doc_after_model is not None
    assert doc_after_model.state == FileState.PENDING


@pytest.mark.parametrize(
    ("keep_alive", "expected_unloads"),
    [
        ("unload_idle", ["embeddinggemma-2"]),
        ("server", []),
        ("keep_loaded", []),
    ],
)
def test_idle_unload_respects_ollama_keep_alive_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    keep_alive: str,
    expected_unloads: list[str],
) -> None:
    """Verify _handle_idle_transition only calls unload() when ollama_keep_alive == 'unload_idle'."""
    db = Database(tmp_path / "index.sqlite")
    doc_path = tmp_path / "note.md"
    doc_path.write_text("Idle VRAM keep-alive policy check.", encoding="utf-8")
    st = doc_path.stat()
    fid = db.insert_file(Candidate(str(doc_path), FileKind.DOC, st.st_size, st.st_mtime, st.st_ino, st.st_dev))

    unloaded: list[str] = []

    class TrackingOllama(MockOllama):
        def unload(self, model: str) -> None:
            unloaded.append(model)

    config = AppConfig(model="embeddinggemma-2", ollama_keep_alive=keep_alive)
    service = IndexService(db=db, ollama=TrackingOllama(), config=config)

    rec = db.find_by_id(fid)
    assert rec is not None
    assert service.process_file(rec, db) is True
    assert bool(service._did_embed_since_idle) is True

    monkeypatch.setattr(service._work_event, "wait", lambda: True)
    service._handle_idle_transition()

    assert service._did_embed_since_idle is False
    assert unloaded == expected_unloads


@pytest.mark.parametrize(("duration_min", "expected_sec"), [(0, 0), (90, 5400)])
def test_extract_segments_forwards_max_media_duration_min(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    duration_min: int,
    expected_sec: int,
) -> None:
    """Verify max_media_duration_min is converted to max_duration_sec for audio and video extractors."""
    db = Database(tmp_path / "index.sqlite")
    audio_path = tmp_path / "track.mp3"
    video_path = tmp_path / "clip.mp4"
    audio_path.write_bytes(b"ID3")
    video_path.write_bytes(b"ftyp")

    st_a = audio_path.stat()
    st_v = video_path.stat()
    fid_a = db.insert_file(
        Candidate(str(audio_path), FileKind.AUDIO, st_a.st_size, st_a.st_mtime, st_a.st_ino, st_a.st_dev)
    )
    fid_v = db.insert_file(
        Candidate(str(video_path), FileKind.VIDEO, st_v.st_size, st_v.st_mtime, st_v.st_ino, st_v.st_dev)
    )

    seen_calls: list[tuple[str, int]] = []

    def fake_audio(_path: str, *, max_duration_sec: int = 3600) -> list[ExtractedSegment]:
        seen_calls.append(("audio", max_duration_sec))
        return []

    def fake_video(_path: str, *, max_duration_sec: int = 3600) -> list[ExtractedSegment]:
        seen_calls.append(("video", max_duration_sec))
        return []

    monkeypatch.setattr("semsearch.services.indexer.probe_media_duration", lambda _p: 0.0)
    monkeypatch.setattr("semsearch.services.indexer.extract_audio", fake_audio)
    monkeypatch.setattr("semsearch.services.indexer.extract_video", fake_video)

    service = IndexService(db=db, config=AppConfig(max_media_duration_min=duration_min))
    rec_a = db.find_by_id(fid_a)
    rec_v = db.find_by_id(fid_v)
    assert rec_a is not None
    assert rec_v is not None

    assert service.process_file(rec_a, db) is True
    assert service.process_file(rec_v, db) is True
    assert seen_calls == [("audio", expected_sec), ("video", expected_sec)]


def test_crawl_and_enqueue_honor_crawler_and_project_rules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify crawl_folder receives rule flags and enqueue_file respects skip_code_projects."""
    root = tmp_path / "docs"
    project_dir = root / "my_app"
    project_dir.mkdir(parents=True)
    (project_dir / "pyproject.toml").write_text("[project]\nname = 'my_app'\n", encoding="utf-8")
    nested_doc = project_dir / "README.md"
    nested_doc.write_text("# Project docs\n", encoding="utf-8")
    nested_shot = project_dir / "screenshot_demo.png"
    nested_shot.write_bytes(b"\x89PNG\r\n\x1a\n")

    db = Database(tmp_path / "index.sqlite")
    captured_kwargs: list[dict[str, object]] = []

    def spy_crawl(root_arg: str, **kwargs: object) -> list[Candidate]:
        del root_arg
        captured_kwargs.append(dict(kwargs))
        return []

    monkeypatch.setattr("semsearch.services.indexer.crawl_folder", spy_crawl)

    cfg_custom = AppConfig(
        folders=[str(root)],
        follow_symlinks=True,
        skip_code_projects=False,
        skip_datasets=False,
    )
    service_custom = IndexService(db=db, config=cfg_custom)
    service_custom.crawl_and_sync(is_code=False)

    assert len(captured_kwargs) == 1
    assert captured_kwargs[0]["follow_symlinks"] is True
    assert captured_kwargs[0]["skip_code_projects"] is False
    assert captured_kwargs[0]["skip_datasets"] is False

    # With skip_code_projects=True, enqueue_file skips docs inside nested projects but allows screenshots.
    db_skip = Database(tmp_path / "skip.sqlite")
    service_skip = IndexService(db=db_skip, config=AppConfig(folders=[str(root)], skip_code_projects=True))
    service_skip.enqueue_file(str(nested_doc))
    service_skip.enqueue_file(str(nested_shot))
    assert db_skip.find_by_path(str(nested_doc.resolve())) is None
    assert db_skip.find_by_path(str(nested_shot.resolve())) is not None

    # With skip_code_projects=False, enqueue_file indexes docs inside nested projects.
    service_custom.enqueue_file(str(nested_doc))
    assert db.find_by_path(str(nested_doc.resolve())) is not None


def test_build_embed_inputs_uses_model_aware_doc_prompts(tmp_path: Path) -> None:
    """Verify EmbeddingGemma uses title/text prompt format while non-Gemma models use plain title + body."""
    doc_path = tmp_path / "report.txt"
    doc_path.write_text("Quarterly financial summary", encoding="utf-8")
    st = doc_path.stat()

    seen: list[tuple[str, list[str | dict[str, str]]]] = []

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
            seen.append((model, list(inputs)))
            return [[0.2] * DIM for _ in inputs]

    ollama = RecordingOllama()

    for model_name in ("embeddinggemma-2", "clip-vit:latest"):
        db = Database(tmp_path / f"{model_name.replace(':', '_')}.sqlite")
        fid = db.insert_file(Candidate(str(doc_path), FileKind.DOC, st.st_size, st.st_mtime, st.st_ino, st.st_dev))
        service = IndexService(db=db, ollama=ollama, config=AppConfig(model=model_name))
        rec = db.find_by_id(fid)
        assert rec is not None
        assert service.process_file(rec, db) is True

    assert seen == [
        ("embeddinggemma-2", ["title: report | text: Quarterly financial summary"]),
        ("clip-vit:latest", ["report\n\nQuarterly financial summary"]),
    ]


def test_consecutive_timeout_cap_marks_failed_and_advances_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify 3 consecutive OllamaTimeoutErrors mark the file FAILED so the worker advances to the next file."""
    stuck_path = tmp_path / "stuck.txt"
    stuck_path.write_text("stuck payload that always times out", encoding="utf-8")
    good_path = tmp_path / "good.txt"
    good_path.write_text("good payload that succeeds", encoding="utf-8")

    db = Database(tmp_path / "index.sqlite")
    st_stuck = stuck_path.stat()
    st_good = good_path.stat()
    fid_stuck = db.insert_file(
        Candidate(str(stuck_path), FileKind.DOC, st_stuck.st_size, st_stuck.st_mtime, st_stuck.st_ino, st_stuck.st_dev)
    )
    fid_good = db.insert_file(
        Candidate(str(good_path), FileKind.DOC, st_good.st_size, st_good.st_mtime, st_good.st_ino, st_good.st_dev)
    )

    stuck_attempts = 0

    class TimeoutOnStuckOllama(MockOllama):
        def embed(
            self,
            _model: str,
            inputs: list[str | dict[str, str]],
            *,
            truncate: bool = True,
            num_ctx: int | None = None,
            keep_alive: int | str | None = None,
        ) -> list[list[float]]:
            del truncate, num_ctx, keep_alive
            nonlocal stuck_attempts
            if any("stuck" in str(item) for item in inputs):
                stuck_attempts += 1
                raise OllamaTimeoutError("embed request timed out")
            return [[0.1] * DIM for _ in inputs]

        def unload(self, _model: str) -> None:
            return None

    service = IndexService(db=db, ollama=TimeoutOnStuckOllama())
    waits: list[float | None] = []

    def record_wait(timeout: float | None = None) -> bool:
        waits.append(timeout)
        return False

    monkeypatch.setattr(service._stop_event, "wait", record_wait)
    monkeypatch.setattr(service._work_event, "wait", service._stop_event.set)

    service._worker_loop()

    assert stuck_attempts == 3
    assert waits == [2.0, 4.0]
    stuck_rec = db.find_by_id(fid_stuck)
    good_rec = db.find_by_id(fid_good)
    assert stuck_rec is not None
    assert good_rec is not None
    assert stuck_rec.state == FileState.FAILED
    assert "embed request timed out" in stuck_rec.detail
    assert good_rec.state == FileState.DONE
    assert service.status.waiting_for == ""
    assert service._timeout_counts == {}


def test_connection_errors_keep_retrying_beyond_timeout_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify OllamaConnectionError never counts toward the timeout failure cap and clears counts on reindex."""
    doc_path = tmp_path / "offline.txt"
    doc_path.write_text("waiting for daemon restart", encoding="utf-8")
    st = doc_path.stat()

    db = Database(tmp_path / "index.sqlite")
    fid = db.insert_file(Candidate(str(doc_path), FileKind.DOC, st.st_size, st.st_mtime, st.st_ino, st.st_dev))

    attempts = 0

    class OfflineThenReadyOllama(MockOllama):
        def embed(
            self,
            _model: str,
            inputs: list[str | dict[str, str]],
            *,
            truncate: bool = True,
            num_ctx: int | None = None,
            keep_alive: int | str | None = None,
        ) -> list[list[float]]:
            del truncate, num_ctx, keep_alive
            nonlocal attempts
            attempts += 1
            if attempts <= 4:
                raise OllamaConnectionError("connection refused")
            return [[0.1] * DIM for _ in inputs]

        def unload(self, _model: str) -> None:
            return None

    service = IndexService(db=db, ollama=OfflineThenReadyOllama())
    monkeypatch.setattr(service._stop_event, "wait", lambda _timeout=None: False)
    monkeypatch.setattr(service._work_event, "wait", service._stop_event.set)

    service._worker_loop()

    assert attempts == 5
    rec = db.find_by_id(fid)
    assert rec is not None
    assert rec.state == FileState.DONE

    service._timeout_counts[(fid, False)] = 2
    service.reset_all_for_reindex()
    assert service._timeout_counts == {}
