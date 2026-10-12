"""Unit tests for SemSearch configuration and core types."""

from pathlib import Path
import pytest
from semsearch.config import AppConfig, ConfigManager
from semsearch.core.types import (
    Candidate,
    EngineActivity,
    EngineStatus,
    FileKind,
    FileRecord,
    FileState,
    Modality,
    ResultRow,
    SearchHit,
    SegmentKind,
    SegmentRecord,
)


def test_default_config_values():
    """Verify default AppConfig attribute values."""
    config = AppConfig()
    assert config.folders == []
    assert config.excluded_folders == []
    assert "heic" in config.excluded_types
    assert config.code_enabled is False
    assert config.code_folders == []
    assert config.code_editor == "auto"
    assert config.ollama_url == "http://localhost:11434"
    assert config.model == "embeddinggemma-2"
    assert config.hotkey == "ctrl+shift+space"
    assert config.index_on_battery is True
    assert config.index_schedule_enabled is False
    assert config.index_schedule_start == 1320
    assert config.index_schedule_end == 420
    assert config.onboarded is False


def test_config_manager_load_and_save(tmp_path: Path):
    """Verify ConfigManager loads defaults, saves to file, and reads back changes."""
    cfg_file = tmp_path / "config.json"
    mgr = ConfigManager(config_path=cfg_file)

    assert mgr.config.model == "embeddinggemma-2"

    mgr.update(model="custom-embed", onboarded=True, code_enabled=True)
    assert cfg_file.exists()

    new_mgr = ConfigManager(config_path=cfg_file)
    assert new_mgr.config.model == "custom-embed"
    assert new_mgr.config.onboarded is True
    assert new_mgr.config.code_enabled is True


def test_config_manager_add_and_remove_folder(tmp_path: Path):
    """Verify folder addition, deduplication, and removal."""
    cfg_file = tmp_path / "config.json"
    mgr = ConfigManager(config_path=cfg_file)

    folder = tmp_path / "docs"
    folder.mkdir()
    folder_str = str(folder.resolve())

    mgr.add_folder(folder_str)
    assert folder_str in mgr.config.folders
    assert len(mgr.config.folders) == 1

    # Adding again should deduplicate
    mgr.add_folder(folder_str)
    assert len(mgr.config.folders) == 1

    # Remove folder
    mgr.remove_folder(folder_str)
    assert folder_str not in mgr.config.folders
    assert len(mgr.config.folders) == 0


def test_core_enums():
    """Verify domain enums values."""
    assert FileKind.SCREENSHOT.value == "screenshot"
    assert FileKind.IMAGE.value == "image"
    assert FileKind.PDF.value == "pdf"
    assert FileKind.DOC.value == "doc"
    assert FileKind.AUDIO.value == "audio"
    assert FileKind.VIDEO.value == "video"
    assert FileKind.CODE.value == "code"

    assert SegmentKind.NAME.value == "name"
    assert SegmentKind.PAGE.value == "page"
    assert SegmentKind.PAGE_IMAGE.value == "page_image"
    assert SegmentKind.LINES.value == "lines"

    assert Modality.TEXT.value == "text"
    assert Modality.IMAGE.value == "image"

    assert FileState.PENDING.value == "pending"
    assert FileState.DONE.value == "done"

    assert EngineActivity.STARTING.value == "starting"
    assert EngineActivity.INDEXING.value == "indexing"
    assert EngineActivity.IDLE.value == "idle"


def test_core_dataclasses():
    """Verify instantiation of core dataclasses."""
    candidate = Candidate(
        path="/tmp/test.pdf",
        kind=FileKind.PDF,
        size=1024,
        mtime=1700000000.0,
        inode=12345,
        device=67890,
    )
    assert candidate.path == "/tmp/test.pdf"
    assert candidate.kind == FileKind.PDF

    record = FileRecord(
        id=1,
        path="/tmp/test.pdf",
        kind=FileKind.PDF,
        size=1024,
        mtime=1700000000.0,
        inode=12345,
        device=67890,
        state=FileState.DONE,
        detail="ok",
        info={"pages": "5"},
        indexed_at=1700000100.0,
    )
    assert record.id == 1
    assert record.state == FileState.DONE

    segment = SegmentRecord(
        id=1,
        file_id=1,
        kind=SegmentKind.PAGE,
        modality=Modality.TEXT,
        loc=1.0,
        loc_end=1.0,
        excerpt="sample page text",
        vec=b"\x00\x01",
    )
    assert segment.excerpt == "sample page text"

    hit = SearchHit(
        path="/tmp/project/src/main.py",
        kind=FileKind.CODE,
        score=0.95,
        z=2.5,
        cosine=0.88,
        keyword_rank=1,
        segment=SegmentKind.LINES,
        segment_id=42,
        loc=10.0,
        loc_end=25.0,
        excerpt="def «connect»(self): pass",
        modified=1700000000.0,
        size=2048,
        info={"repo": "/tmp/project"},
        focus=12.0,
    )
    assert hit.score == 0.95

    row = ResultRow.from_hit(hit)
    assert row.title == "main.py"
    assert row.folder == "/tmp/project/src"
    assert row.place == "L10–25"
    assert row.matched_meaning is True
    assert row.matched_words is True
    assert row.found_words == ["connect"]
    assert row.code_line == 12

    status = EngineStatus(
        activity=EngineActivity.INDEXING,
        pending=5,
        indexed=10,
        total=15,
        current_file="/tmp/test.pdf",
    )
    assert status.activity == EngineActivity.INDEXING
    assert status.total == 15
