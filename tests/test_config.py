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
    SearchHit,
    SegmentKind,
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
    assert config.num_ctx == 0
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

    mgr.config.model = "custom-embed"
    mgr.config.onboarded = True
    mgr.config.code_enabled = True
    mgr.save()
    assert cfg_file.exists()

    new_mgr = ConfigManager(config_path=cfg_file)
    assert new_mgr.config.model == "custom-embed"
    assert new_mgr.config.onboarded is True
    assert new_mgr.config.code_enabled is True


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
    assert hit.reason == "Matches meaning"

    status = EngineStatus(
        activity=EngineActivity.INDEXING,
        pending=5,
        indexed=10,
        total=15,
        current_file="/tmp/test.pdf",
    )
    assert status.activity == EngineActivity.INDEXING
    assert status.total == 15


def test_search_hit_reasons():
    """Verify SearchHit.reason for meaning, keyword, and name matches."""
    hit_page = SearchHit(
        path="/tmp/doc.pdf",
        kind=FileKind.PDF,
        score=0.5,
        segment=SegmentKind.PAGE,
        loc=3.0,
    )
    assert hit_page.reason == "Matches name"

    hit_audio = SearchHit(
        path="/tmp/audio.mp3",
        kind=FileKind.AUDIO,
        score=0.8,
        keyword_rank=2,
        segment=SegmentKind.AUDIO,
        loc=65.0,
    )
    assert hit_audio.reason == "Matches words"


def test_config_manager_filters_unknown_fields(tmp_path: Path):
    """Verify ConfigManager ignores extraneous fields in config file."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text('{"extra_key": "ignore_me", "model": "test-embed"}', encoding="utf-8")
    mgr = ConfigManager(config_path=cfg_file)
    assert mgr.config.model == "test-embed"
    assert not hasattr(mgr.config, "extra_key")

