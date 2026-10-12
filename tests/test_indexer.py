"""Tests for IndexService, priority queue tiers, pipelining, and partial continuation."""

from pathlib import Path
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
from semsearch.services.indexer import IndexQueueTier, IndexService, MeasuredCosts


class MockOllama(OllamaClient):
    def embed(self, model: str, inputs: list[str | dict[str, str]], truncate: bool = True) -> list[list[float]]:
        return [[0.1] * DIM for _ in inputs]


def test_measured_costs_tracking_and_estimates():
    costs = MeasuredCosts()

    # Initial estimates
    est_time = costs.estimate_time({"image": 5, "doc": 10})
    assert est_time > 0.0

    est_disk = costs.estimate_disk_growth(10)
    assert est_disk > 0

    # Update metrics
    costs.update(Modality.TEXT, elapsed_ms=100.0, count=2, bytes_added=3200)
    assert costs.ms_per_chunk == 50.0
    assert costs.bytes_per_segment == 1600

    costs.update(Modality.IMAGE, elapsed_ms=200.0, count=1)
    assert costs.ms_per_image == 200.0


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
    assert fid in service._new_files_queue

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

    monkeypatch.setattr("semsearch.services.indexer.extract_doc_segments", failing_extract)

    service = IndexService(db=db)
    rec = db.find_by_id(fid)
    assert rec is not None

    success = service.process_file(rec, db)
    assert success is False

    updated_rec = db.find_by_id(fid)
    assert updated_rec is not None
    assert updated_rec.state == FileState.FAILED
    assert "Extraction failed due to parser crash" in (updated_rec.detail or "")
