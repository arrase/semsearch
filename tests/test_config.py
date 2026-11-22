"""Unit tests for SemSearch configuration and core types."""

import stat
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from semsearch.config import (
    AppConfig,
    ConfigManager,
    get_autostart_path,
    get_default_config_path,
    get_thumbs_cache_dir,
    resolve_data_dir,
    resolve_xdg_cache_home,
    resolve_xdg_config_home,
    resolve_xdg_data_home,
)
from semsearch.core.types import (
    Candidate,
    EngineActivity,
    EngineStatus,
    FileKind,
    FileRecord,
    FileState,
    MatchReason,
    Modality,
    ParsedQuery,
    SearchHit,
    SegmentKind,
)
from semsearch.i18n import set_language


def test_default_config_values() -> None:
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
    assert config.data_dir == ""
    assert config.follow_symlinks is False
    assert config.skip_code_projects is True
    assert config.skip_datasets is True
    assert config.max_media_duration_min == 60
    assert config.code_watch_delay_sec == 60
    assert config.ollama_keep_alive == "unload_idle"
    assert config.ollama_timeout == 120
    assert config.ollama_api_key == ""
    assert config.custom_editor_cmd == ""
    assert config.terminal == "auto"
    assert config.custom_terminal_cmd == ""
    assert config.theme == "dark"
    assert config.close_on_focus_loss is True
    assert config.remember_last_query is False


def test_config_manager_load_and_save(tmp_path: Path) -> None:
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


def test_config_manager_atomic_save_permissions(tmp_path: Path) -> None:
    """Verify ConfigManager.save writes atomically with 0o600 permissions and no residual temp file."""
    cfg_file = tmp_path / "nested" / "config.json"
    tmp_file = cfg_file.with_name(f".{cfg_file.name}.tmp")
    mgr = ConfigManager(config_path=cfg_file)

    mgr.config.model = "atomic-embed"
    mgr.save()

    assert cfg_file.is_file()
    assert not tmp_file.exists()
    assert stat.S_IMODE(cfg_file.stat().st_mode) == 0o600

    mgr.config.num_ctx = 4096
    mgr.save()

    assert not tmp_file.exists()
    assert stat.S_IMODE(cfg_file.stat().st_mode) == 0o600
    assert ConfigManager(config_path=cfg_file).config.num_ctx == 4096


def test_config_manager_resilient_load_corrupt_or_invalid(tmp_path: Path) -> None:
    """Verify ConfigManager falls back cleanly on truncated, corrupt, or mistyped config files."""
    cfg_file = tmp_path / "config.json"

    # Truncated JSON
    cfg_file.write_text('{"model": "broken', encoding="utf-8")
    assert ConfigManager(config_path=cfg_file).config == AppConfig()

    # Non-dict JSON roots
    for payload in ("[]", '"just a string"', "42", "null"):
        cfg_file.write_text(payload, encoding="utf-8")
        assert ConfigManager(config_path=cfg_file).config == AppConfig()

    # Corrupt non-UTF-8 bytes
    cfg_file.write_bytes(b"\xff\xfe\x00\x80")
    assert ConfigManager(config_path=cfg_file).config == AppConfig()

    # Mistyped field values do not break AppConfig construction
    cfg_file.write_text(
        '{"model": 123, "num_ctx": "bad", "folders": ["ok", 42], "code_enabled": "yes", "onboarded": true}',
        encoding="utf-8",
    )
    loaded = ConfigManager(config_path=cfg_file).config
    assert loaded.model == AppConfig().model
    assert loaded.num_ctx == 0
    assert loaded.folders == []
    assert loaded.code_enabled is False
    assert loaded.onboarded is True


def test_config_manager_add_and_remove_folder(tmp_path: Path) -> None:
    """Verify add_folder and remove_folder normalize paths, deduplicate, and persist changes."""
    cfg_file = tmp_path / "config.json"
    docs_dir = tmp_path / "docs"
    repo_dir = tmp_path / "repo"
    docs_dir.mkdir()
    repo_dir.mkdir()

    mgr = ConfigManager(config_path=cfg_file)
    unnormalized_docs = str(docs_dir / "." / ".." / "docs")
    expected_docs = str(docs_dir.resolve())
    expected_repo = str(repo_dir.resolve())

    assert mgr.add_folder(unnormalized_docs) is True
    assert mgr.config.folders == [expected_docs]
    assert ConfigManager(config_path=cfg_file).config.folders == [expected_docs]

    # Duplicate ignored without modifying list
    assert mgr.add_folder(str(docs_dir)) is False
    assert mgr.config.folders == [expected_docs]

    # Code folders managed independently
    assert mgr.add_folder(str(repo_dir), is_code=True) is True
    assert mgr.add_folder(str(repo_dir), is_code=True) is False
    assert mgr.config.code_folders == [expected_repo]
    assert mgr.config.folders == [expected_docs]

    # Remove existing and non-existing folders
    assert mgr.remove_folder(unnormalized_docs) is True
    assert mgr.remove_folder(str(docs_dir)) is False
    assert ConfigManager(config_path=cfg_file).config.folders == []

    assert mgr.remove_folder(str(repo_dir), is_code=True) is True
    assert mgr.remove_folder(str(repo_dir), is_code=True) is False
    assert ConfigManager(config_path=cfg_file).config.code_folders == []


def test_core_enums() -> None:
    """Verify domain enum values and strict Zero-OCR invariant."""
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
    assert not hasattr(SegmentKind, "OCR")
    assert "ocr" not in {member.value for member in SegmentKind}

    assert Modality.TEXT.value == "text"
    assert Modality.IMAGE.value == "image"

    assert FileState.PENDING.value == "pending"
    assert FileState.DONE.value == "done"

    assert EngineActivity.STARTING.value == "starting"
    assert EngineActivity.INDEXING.value == "indexing"
    assert EngineActivity.IDLE.value == "idle"

    assert MatchReason.MEANING.value == "Matches meaning"
    assert MatchReason.WORDS.value == "Matches words"
    assert MatchReason.NAME.value == "Matches name"


def test_parsed_query_dataclass() -> None:
    """Verify ParsedQuery defaults, filter fields, and immutability."""
    default_q = ParsedQuery(clean_query="budget report")
    assert default_q.clean_query == "budget report"
    assert default_q.is_code is False
    assert default_q.kinds == frozenset()
    assert default_q.extensions == frozenset()
    assert default_q.path_fragments == ()
    assert default_q.after_mtime == 0.0

    filtered_q = ParsedQuery(
        clean_query="auth token",
        is_code=True,
        kinds=frozenset({FileKind.CODE, FileKind.PDF}),
        extensions=frozenset({"py", "ts"}),
        path_fragments=("src/auth",),
        after_mtime=1700000000.0,
    )
    assert filtered_q.is_code is True
    assert filtered_q.kinds == frozenset({FileKind.CODE, FileKind.PDF})
    assert filtered_q.extensions == frozenset({"py", "ts"})
    assert filtered_q.path_fragments == ("src/auth",)
    assert filtered_q.after_mtime == 1700000000.0

    field_name = "clean_query"
    with pytest.raises(FrozenInstanceError):
        setattr(filtered_q, field_name, "other")


def test_core_dataclasses() -> None:
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
    assert hit.reason is MatchReason.MEANING
    assert hit.reason.value == "Matches meaning"

    status = EngineStatus(
        activity=EngineActivity.INDEXING,
        pending=5,
        indexed=10,
        total=15,
        current_file="/tmp/test.pdf",
    )
    assert status.activity == EngineActivity.INDEXING
    assert status.total == 15


def test_search_hit_reasons() -> None:
    """Verify SearchHit.reason returns domain MatchReason independent of active locale."""
    set_language("es")
    hit_meaning = SearchHit(path="/tmp/a.pdf", kind=FileKind.PDF, score=0.9, z=2.5)
    assert hit_meaning.reason is MatchReason.MEANING

    hit_page = SearchHit(
        path="/tmp/doc.pdf",
        kind=FileKind.PDF,
        score=0.5,
        segment=SegmentKind.PAGE,
        loc=3.0,
    )
    assert hit_page.reason is MatchReason.NAME

    hit_audio = SearchHit(
        path="/tmp/audio.mp3",
        kind=FileKind.AUDIO,
        score=0.8,
        keyword_rank=2,
        segment=SegmentKind.AUDIO,
        loc=65.0,
    )
    assert hit_audio.reason is MatchReason.WORDS


def test_config_manager_filters_unknown_fields(tmp_path: Path) -> None:
    """Verify ConfigManager ignores extraneous fields in config file."""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text('{"extra_key": "ignore_me", "model": "test-embed"}', encoding="utf-8")
    mgr = ConfigManager(config_path=cfg_file)
    assert mgr.config.model == "test-embed"
    assert not hasattr(mgr.config, "extra_key")


def test_xdg_path_helpers_and_default_config_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify XDG helpers resolve environment overrides and fall back to standard home paths."""
    cfg_home = tmp_path / "xdg_config"
    data_home = tmp_path / "xdg_data"
    cache_home = tmp_path / "xdg_cache"

    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))

    assert resolve_xdg_config_home() == cfg_home
    assert resolve_xdg_data_home() == data_home
    assert resolve_xdg_cache_home() == cache_home
    assert get_default_config_path() == cfg_home / "semsearch" / "config.json"
    assert get_autostart_path() == cfg_home / "autostart" / "semsearch.desktop"
    assert get_thumbs_cache_dir() == cache_home / "semsearch" / "thumbs"
    assert ConfigManager().config_path == cfg_home / "semsearch" / "config.json"

    # Whitespace-only or unset env vars fall back to standard defaults
    monkeypatch.setenv("XDG_CONFIG_HOME", "   ")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

    assert resolve_xdg_config_home() == Path.home() / ".config"
    assert resolve_xdg_data_home() == Path.home() / ".local" / "share"
    assert resolve_xdg_cache_home() == Path.home() / ".cache"
    assert get_default_config_path() == Path.home() / ".config" / "semsearch" / "config.json"
    assert get_autostart_path() == Path.home() / ".config" / "autostart" / "semsearch.desktop"
    assert get_thumbs_cache_dir() == Path.home() / ".cache" / "semsearch" / "thumbs"


def test_resolve_data_dir_override_and_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify resolve_data_dir prefers config.data_dir and otherwise uses XDG_DATA_HOME."""
    xdg_data = tmp_path / "xdg_data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))

    assert resolve_data_dir(AppConfig()) == xdg_data / "semsearch"
    assert resolve_data_dir(AppConfig(data_dir="   ")) == xdg_data / "semsearch"

    custom_dir = tmp_path / "custom" / ".." / "store"
    assert resolve_data_dir(AppConfig(data_dir=str(custom_dir))) == (tmp_path / "store").resolve()


def test_config_manager_validates_enum_and_numeric_bounds(tmp_path: Path) -> None:
    """Verify invalid enum values and out-of-range integers fall back to AppConfig defaults."""
    cfg_file = tmp_path / "config.json"

    # Invalid values fall back to defaults
    cfg_file.write_text(
        (
            '{"ollama_keep_alive": "forever", "theme": "neon", '
            '"ollama_timeout": 4, "max_media_duration_min": -1, "code_watch_delay_sec": 0}'
        ),
        encoding="utf-8",
    )
    invalid_loaded = ConfigManager(config_path=cfg_file).config
    assert invalid_loaded.ollama_keep_alive == "unload_idle"
    assert invalid_loaded.theme == "dark"
    assert invalid_loaded.ollama_timeout == 120
    assert invalid_loaded.max_media_duration_min == 60
    assert invalid_loaded.code_watch_delay_sec == 60

    # Boundary valid values are accepted and persisted
    cfg_file.write_text(
        (
            '{"ollama_keep_alive": "keep_loaded", "theme": "system", '
            '"ollama_timeout": 5, "max_media_duration_min": 0, "code_watch_delay_sec": 1, '
            '"follow_symlinks": true, "skip_code_projects": false, "skip_datasets": false, '
            '"ollama_api_key": "secret-token", "custom_editor_cmd": "hx {file}:{line}", '
            '"terminal": "kitty", "custom_terminal_cmd": "alacritty -e", '
            '"close_on_focus_loss": false, "remember_last_query": true}'
        ),
        encoding="utf-8",
    )
    valid_loaded = ConfigManager(config_path=cfg_file).config
    assert valid_loaded.ollama_keep_alive == "keep_loaded"
    assert valid_loaded.theme == "system"
    assert valid_loaded.ollama_timeout == 5
    assert valid_loaded.max_media_duration_min == 0
    assert valid_loaded.code_watch_delay_sec == 1
    assert valid_loaded.follow_symlinks is True
    assert valid_loaded.skip_code_projects is False
    assert valid_loaded.skip_datasets is False
    assert valid_loaded.ollama_api_key == "secret-token"
    assert valid_loaded.custom_editor_cmd == "hx {file}:{line}"
    assert valid_loaded.terminal == "kitty"
    assert valid_loaded.custom_terminal_cmd == "alacritty -e"
    assert valid_loaded.close_on_focus_loss is False
    assert valid_loaded.remember_last_query is True


def test_config_manager_rejects_path_outside_allowed_directories() -> None:
    """Verify ConfigManager rejects config paths escaping allowed user directories."""
    with pytest.raises(ValueError, match="outside allowed directories"):
        ConfigManager(config_path=Path("/etc/semsearch-unauthorized.json"))
