"""Unit tests for headless CLI commands and user-space desktop integration."""

import ast
import json
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

import semsearch.cli as cli_module
import semsearch.main as main_module
from semsearch.cli import (
    get_default_data_dir,
    hit_to_dict,
    install_desktop_integration,
    run_install_desktop,
    run_query,
    run_reindex,
    run_status,
)
from semsearch.config import ConfigManager
from semsearch.core.database import CODE_DB_FILE, MAIN_DB_FILE, Database
from semsearch.core.ollama import OllamaClient
from semsearch.core.types import Candidate, FileKind, FileState, Modality, SearchHit, SegmentKind
from semsearch.core.vectors import float16_to_bytes, l2_normalize


class _StubOllamaClient(OllamaClient):
    """Offline OllamaClient test double returning deterministic embeddings without network calls."""

    def __init__(self, embedding: list[float] | None = None) -> None:
        super().__init__(base_url="http://127.0.0.1:11434")
        self._embedding = embedding
        self.closed = False

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
        if self._embedding is None:
            return []
        return [list(self._embedding) for _ in inputs]

    def close(self) -> None:
        self.closed = True
        super().close()


def _seed_databases(tmp_path: Path, *, code_enabled: bool = True) -> tuple[ConfigManager, Path]:
    cfg_path = tmp_path / "config" / "config.json"
    cfg_manager = ConfigManager(cfg_path)
    cfg_manager.config.code_enabled = code_enabled
    cfg_manager.config.model = "embeddinggemma-2"
    cfg_manager.save()

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    vec = float16_to_bytes(l2_normalize(np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)))

    with Database(data_dir / MAIN_DB_FILE) as db:
        db.set_meta("embedding_model", "embeddinggemma-2")
        db.set_meta("embedding_dim", "4")

        pdf_id = db.insert_file(Candidate("/docs/quarterly_budget.pdf", FileKind.PDF, 4096, 1700000000.0, 10, 1))
        db.insert_segment(
            pdf_id,
            SegmentKind.PAGE,
            Modality.TEXT,
            loc=3.0,
            excerpt="Quarterly budget forecast for Q4",
            body="Quarterly budget forecast for Q4",
            vec=vec,
        )
        db.update_file_state(pdf_id, FileState.DONE)

        doc_id = db.insert_file(Candidate("/docs/quarterly_notes.md", FileKind.DOC, 1024, 1700000100.0, 11, 1))
        db.insert_segment(
            doc_id,
            SegmentKind.CHUNK,
            Modality.TEXT,
            loc=1.0,
            excerpt="Meeting notes about quarterly roadmap",
            body="Meeting notes about quarterly roadmap",
        )
        db.update_file_state(doc_id, FileState.DONE)

        audio_id = db.insert_file(Candidate("/media/standup_budget.mp3", FileKind.AUDIO, 2048, 1700000200.0, 12, 1))
        db.insert_segment(
            audio_id,
            SegmentKind.AUDIO,
            Modality.AUDIO,
            loc=75.0,
            excerpt="Audio discussion about budget",
            body="Audio discussion about budget",
        )
        db.update_file_state(audio_id, FileState.DONE)

    if code_enabled:
        with Database(data_dir / CODE_DB_FILE) as code_db:
            code_db.set_meta("embedding_model", "embeddinggemma-2")
            code_db.set_meta("embedding_dim", "4")
            code_id = code_db.insert_file(Candidate("/repo/billing.py", FileKind.CODE, 512, 1700000300.0, 20, 1))
            code_db.insert_segment(
                code_id,
                SegmentKind.LINES,
                Modality.TEXT,
                loc=10.0,
                loc_end=25.0,
                excerpt="def compute_invoice_total():",
                body="def compute_invoice_total():\n    return 42\n",
                focus=10.0,
            )
            code_db.update_file_state(code_id, FileState.DONE)

    return cfg_manager, data_dir


def test_cli_module_has_no_pyside6_imports() -> None:
    """cli.py itself must not import PySide6 to keep headless CLI invocations lightweight."""
    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)
    assert not any(name.startswith("PySide6") for name in imported_modules)


def test_get_default_data_dir_respects_xdg_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    custom = tmp_path / "xdg_data"
    monkeypatch.setenv("XDG_DATA_HOME", str(custom))
    assert get_default_data_dir() == custom / "semsearch"

    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert get_default_data_dir() == Path.home() / ".local" / "share" / "semsearch"


def test_hit_to_dict_serializes_all_expected_fields() -> None:
    hit = SearchHit(
        path="/docs/report.pdf",
        kind=FileKind.PDF,
        score=3.1415926,
        z=2.75,
        cosine=0.8123,
        keyword_rank=2,
        segment=SegmentKind.PAGE,
        segment_id=9,
        loc=4.0,
        loc_end=4.0,
        focus=4.0,
        excerpt="Revenue overview",
        modified=1700000000.0,
        size=8192,
    )
    data = hit_to_dict(hit)
    assert data == {
        "path": "/docs/report.pdf",
        "kind": "pdf",
        "score": 3.1416,
        "z": 2.75,
        "cosine": 0.8123,
        "keyword_rank": 2,
        "segment": "page",
        "loc": 4.0,
        "loc_end": 4.0,
        "focus": 4.0,
        "excerpt": "Revenue overview",
        "reason": "Matches meaning",
        "modified": 1700000000.0,
        "size": 8192,
    }


def test_run_query_json_and_text_outputs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg_manager, data_dir = _seed_databases(tmp_path)
    ollama = _StubOllamaClient()
    try:
        rc = run_query(
            "quarterly",
            as_json=True,
            config_manager=cfg_manager,
            data_dir=data_dir,
            ollama_client=ollama,
        )
        assert rc == 0
        assert not ollama.closed
        payload = json.loads(capsys.readouterr().out)
        paths = [item["path"] for item in payload]
        assert "/docs/quarterly_budget.pdf" in paths
        assert "/docs/quarterly_notes.md" in paths
        assert all("score" in item and "reason" in item for item in payload)

        rc_text = run_query(
            "budget forecast",
            as_json=False,
            config_manager=cfg_manager,
            data_dir=data_dir,
            ollama_client=ollama,
        )
        assert rc_text == 0
        out_text = capsys.readouterr().out
        assert "/docs/quarterly_budget.pdf (p.3)" in out_text
        assert "pdf" in out_text

        rc_audio = run_query(
            "audio discussion",
            as_json=False,
            config_manager=cfg_manager,
            data_dir=data_dir,
            ollama_client=ollama,
        )
        assert rc_audio == 0
        assert "/media/standup_budget.mp3 (01:15)" in capsys.readouterr().out
    finally:
        ollama.close()


def test_run_query_code_and_kind_filters(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg_manager, data_dir = _seed_databases(tmp_path)
    ollama = _StubOllamaClient()
    try:
        rc_code = run_query(
            "compute_invoice_total",
            is_code=True,
            as_json=False,
            config_manager=cfg_manager,
            data_dir=data_dir,
            ollama_client=ollama,
        )
        assert rc_code == 0
        out_code = capsys.readouterr().out
        assert "/repo/billing.py:10" in out_code

        rc_kind = run_query(
            "quarterly",
            kinds=["PDF,DOC"],
            as_json=True,
            config_manager=cfg_manager,
            data_dir=data_dir,
            ollama_client=ollama,
        )
        assert rc_kind == 0
        items = json.loads(capsys.readouterr().out)
        assert {item["kind"] for item in items} <= {"pdf", "doc"}
        assert len(items) == 2

        rc_pdf_only = run_query(
            "quarterly",
            kinds=["  pdf "],
            as_json=True,
            config_manager=cfg_manager,
            data_dir=data_dir,
            ollama_client=ollama,
        )
        assert rc_pdf_only == 0
        pdf_items = json.loads(capsys.readouterr().out)
        assert [item["path"] for item in pdf_items] == ["/docs/quarterly_budget.pdf"]
    finally:
        ollama.close()


def test_run_query_invalid_kind_returns_exit_code_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg_manager, data_dir = _seed_databases(tmp_path)
    rc = run_query(
        "quarterly",
        kinds=["pdf,not_a_kind"],
        config_manager=cfg_manager,
        data_dir=data_dir,
    )
    assert rc == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not_a_kind" in captured.err


def test_run_status_json_and_text(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg_manager, data_dir = _seed_databases(tmp_path, code_enabled=True)

    rc_json = run_status(as_json=True, config_manager=cfg_manager, data_dir=data_dir)
    assert rc_json == 0
    data = json.loads(capsys.readouterr().out)
    assert data["configured_model"] == "embeddinggemma-2"
    assert data["code_enabled"] is True
    assert data["main"]["files"] == 3
    assert data["main"]["vectors"] == 1
    assert data["main"]["embedding_model"] == "embeddinggemma-2"
    assert data["main"]["embedding_dim"] == 4
    assert data["main"]["disk_bytes"] > 0
    assert data["code"]["files"] == 1
    assert data["code"]["embedding_model"] == "embeddinggemma-2"
    assert data["code"]["embedding_dim"] == 4

    rc_text = run_status(as_json=False, config_manager=cfg_manager, data_dir=data_dir)
    assert rc_text == 0
    text = capsys.readouterr().out
    assert "Main index: 3 files" in text
    assert "model=embeddinggemma-2" in text
    assert "dim=4" in text
    assert "Code index: 1 files" in text


def test_run_status_without_code_db(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg_manager, data_dir = _seed_databases(tmp_path, code_enabled=False)
    rc = run_status(as_json=True, config_manager=cfg_manager, data_dir=data_dir)
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["code"] is None


def test_run_reindex_forwards_to_ipc_when_daemon_running(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    forwarded: list[str] = []

    def fake_ipc(cmd: str) -> bool:
        forwarded.append(cmd)
        return True

    rc_normal = run_reindex(force=False, forward_ipc=fake_ipc, data_dir=tmp_path / "unused")
    assert rc_normal == 0
    assert forwarded == ["REINDEX"]
    assert "REINDEX" in capsys.readouterr().out
    assert not (tmp_path / "unused").exists()

    rc_force = run_reindex(force=True, forward_ipc=fake_ipc, data_dir=tmp_path / "unused")
    assert rc_force == 0
    assert forwarded == ["REINDEX", "REINDEX_FORCE"]
    assert "REINDEX_FORCE" in capsys.readouterr().out


def test_run_reindex_standalone_with_force_resets_and_crawls(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    doc_file = docs_dir / "guide.md"
    doc_file.write_text("# SemSearch CLI Guide\n", encoding="utf-8")

    code_dir = tmp_path / "code"
    code_dir.mkdir()
    code_file = code_dir / "app.py"
    code_file.write_text("def run() -> None:\n    pass\n", encoding="utf-8")

    cfg_manager = ConfigManager(tmp_path / "config.json")
    cfg_manager.config.folders = [str(docs_dir)]
    cfg_manager.config.code_enabled = True
    cfg_manager.config.code_folders = [str(code_dir)]
    cfg_manager.save()

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Pre-seed an already DONE entry to verify force=True resets it to pending and clears non-name segments.
    st = doc_file.stat()
    with Database(data_dir / MAIN_DB_FILE) as db:
        fid = db.insert_file(
            Candidate(str(doc_file.resolve()), FileKind.DOC, st.st_size, st.st_mtime, st.st_ino, st.st_dev)
        )
        db.insert_segment(fid, SegmentKind.CHUNK, Modality.TEXT, excerpt="Old chunk", body="Old chunk")
        db.update_file_state(fid, FileState.DONE)

    ollama = _StubOllamaClient()
    try:
        rc = run_reindex(
            force=True,
            forward_ipc=lambda _cmd: False,
            config_manager=cfg_manager,
            data_dir=data_dir,
            ollama_client=ollama,
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "Reset 1 files" in out
        assert "Synced index: 2 files (2 pending" in out

        with Database(data_dir / MAIN_DB_FILE) as db:
            rec = db.find_by_path(str(doc_file.resolve()))
            assert rec is not None
            assert rec.state == FileState.PENDING
            assert db.get_file_segments(rec.id) == []

        with Database(data_dir / CODE_DB_FILE) as code_db:
            code_rec = code_db.find_by_path(str(code_file.resolve()))
            assert code_rec is not None
            assert code_rec.state == FileState.PENDING
    finally:
        ollama.close()


def test_install_desktop_integration_writes_exact_user_space_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exec_bin = "/usr/local/bin/semsearch"
    created = install_desktop_integration(data_home=tmp_path, exec_path=exec_bin)
    assert len(created) == 5
    assert all(path.is_file() for path in created)

    desktop_file = tmp_path / "applications" / "semsearch.desktop"
    icon_file = tmp_path / "icons" / "hicolor" / "scalable" / "apps" / "semsearch.svg"
    dbus_file = tmp_path / "dbus-1" / "services" / "org.semsearch.DesktopSearch.service"
    krunner_file = tmp_path / "krunner" / "dbusplugins" / "semsearch.desktop"
    gnome_file = tmp_path / "gnome-shell" / "search-providers" / "semsearch-search-provider.ini"

    assert created == [desktop_file, icon_file, dbus_file, krunner_file, gnome_file]

    desktop_text = desktop_file.read_text(encoding="utf-8")
    assert f'Exec="{exec_bin}" --toggle' in desktop_text
    assert "Icon=semsearch" in desktop_text
    assert "Categories=Utility;Search;" in desktop_text
    assert "StartupWMClass=semsearch" in desktop_text
    assert "[Desktop Action Settings]" in desktop_text
    assert f'Exec="{exec_bin}" --settings' in desktop_text

    source_svg = Path(cli_module.__file__).parent / "gui" / "icons" / "semsearch.svg"
    assert icon_file.read_bytes() == source_svg.read_bytes()

    dbus_text = dbus_file.read_text(encoding="utf-8")
    assert "[D-BUS Service]" in dbus_text
    assert "Name=org.semsearch.DesktopSearch" in dbus_text
    assert f'Exec="{exec_bin}" --daemon' in dbus_text

    krunner_text = krunner_file.read_text(encoding="utf-8")
    assert "Type=Service" in krunner_text
    assert "Name=SemSearch" in krunner_text
    assert "Icon=semsearch" in krunner_text
    assert "X-Plasma-API=DBus" in krunner_text
    assert "X-Plasma-DBusRunner-Service=org.semsearch.DesktopSearch" in krunner_text
    assert "X-Plasma-DBusRunner-Path=/krunner" in krunner_text
    assert "X-Plasma-Request-Actions-Once=true" in krunner_text

    gnome_text = gnome_file.read_text(encoding="utf-8")
    assert "[Shell Search Provider]" in gnome_text
    assert "Desktop Id=semsearch.desktop" in gnome_text
    assert "BusName=org.semsearch.DesktopSearch" in gnome_text
    assert "ObjectPath=/org/semsearch/SearchProvider" in gnome_text
    assert "Version=2" in gnome_text

    rc = run_install_desktop(data_home=tmp_path, exec_path=exec_bin)
    assert rc == 0
    stdout = capsys.readouterr().out
    for path in created:
        assert str(path) in stdout
    assert "/usr/share/gnome-shell/search-providers/" in stdout


def test_cli_uses_config_data_dir_when_not_overridden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify run_status, run_query, and run_reindex resolve data_dir from AppConfig when omitted."""
    cfg_manager, data_dir = _seed_databases(tmp_path)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "note.md").write_text("Custom data dir test note.\n", encoding="utf-8")

    cfg_manager.config.data_dir = str(data_dir)
    cfg_manager.config.folders = [str(docs_dir)]
    cfg_manager.save()

    # Point XDG_DATA_HOME elsewhere to prove config.data_dir wins over XDG_DATA_HOME.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "unused_xdg_data"))

    rc_status = run_status(as_json=True, config_manager=cfg_manager)
    assert rc_status == 0
    status_data = json.loads(capsys.readouterr().out)
    assert status_data["data_dir"] == str(data_dir.resolve())
    assert status_data["main"]["files"] == 3

    with _StubOllamaClient() as ollama:
        rc_query = run_query("quarterly", as_json=True, config_manager=cfg_manager, ollama_client=ollama)
        assert rc_query == 0
        query_items = json.loads(capsys.readouterr().out)
        assert any(item["path"] == "/docs/quarterly_budget.pdf" for item in query_items)

        rc_reindex = run_reindex(
            force=False,
            forward_ipc=lambda _cmd: False,
            config_manager=cfg_manager,
            ollama_client=ollama,
        )
        assert rc_reindex == 0
        assert "Synced index:" in capsys.readouterr().out


def test_main_config_flag_overrides_config_path_for_cli_and_gui(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify --config parses cleanly and propagates ConfigManager to subcommands and SemSearchApp."""
    custom_cfg_path = tmp_path / "alt" / "custom_config.json"
    custom_data_dir = tmp_path / "alt_data"
    custom_data_dir.mkdir()

    cfg_mgr = ConfigManager(config_path=custom_cfg_path)
    cfg_mgr.config.model = "custom-cli-model"
    cfg_mgr.config.data_dir = str(custom_data_dir)
    cfg_mgr.config.language = "en"
    cfg_mgr.save()

    parser = main_module.build_parser()
    parsed = parser.parse_args(["--config", str(custom_cfg_path), "status", "--json"])
    assert parsed.config == custom_cfg_path
    assert parsed.command == "status"

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "ignored_xdg_data"))
    assert main_module.main(["--config", str(custom_cfg_path), "status", "--json"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["configured_model"] == "custom-cli-model"
    assert status_payload["data_dir"] == str(custom_data_dir.resolve())

    # Verify GUI cold-start forwards the --config ConfigManager to SemSearchApp
    monkeypatch.setattr(main_module, "forward_ipc_command", lambda _action: False)
    fake_app = MagicMock()
    fake_app.is_shutdown = True
    app_ctor = MagicMock(return_value=fake_app)
    monkeypatch.setattr(main_module, "SemSearchApp", app_ctor)

    assert main_module.main(["--config", str(custom_cfg_path), "--daemon"]) == 0
    app_ctor.assert_called_once()
    passed_cfg_mgr = app_ctor.call_args.kwargs["config_manager"]
    assert isinstance(passed_cfg_mgr, ConfigManager)
    assert passed_cfg_mgr.config_path == custom_cfg_path.resolve()
    assert passed_cfg_mgr.config.model == "custom-cli-model"


def test_cli_constructs_ollama_client_with_full_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify run_query and run_reindex forward all OllamaClient config fields."""
    cfg_manager, data_dir = _seed_databases(tmp_path)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    cfg_manager.config.folders = [str(docs_dir)]
    cfg_manager.config.ollama_url = "http://remote-ollama:11435"
    cfg_manager.config.ollama_timeout = 45
    cfg_manager.config.num_ctx = 4096
    cfg_manager.config.ollama_api_key = "secret-token"
    cfg_manager.config.ollama_keep_alive = "keep_loaded"
    cfg_manager.save()

    captured_kwargs: list[dict[str, object]] = []

    def fake_ollama_ctor(**kwargs: object) -> _StubOllamaClient:
        captured_kwargs.append(dict(kwargs))
        return _StubOllamaClient()

    monkeypatch.setattr(cli_module, "OllamaClient", fake_ollama_ctor)

    assert run_query("quarterly", config_manager=cfg_manager, data_dir=data_dir) == 0
    assert run_reindex(force=False, forward_ipc=lambda _cmd: False, config_manager=cfg_manager, data_dir=data_dir) == 0

    expected = {
        "base_url": "http://remote-ollama:11435",
        "embed_timeout": 45.0,
        "num_ctx": 4096,
        "api_key": "secret-token",
        "keep_alive": "keep_loaded",
    }
    assert captured_kwargs == [expected, expected]
