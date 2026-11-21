"""Cross-cutting E2E integration tests: WAL concurrency, model switch, Ollama recovery, SVG/DOC, CLI & D-Bus."""

import json
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

import semsearch.services.indexer as indexer_mod
from semsearch.cli import run_query, run_status
from semsearch.config import AppConfig, ConfigManager
from semsearch.core.database import CODE_DB_FILE, MAIN_DB_FILE, Database
from semsearch.core.ollama import OllamaClient, OllamaConnectionError
from semsearch.core.rules import classify_file
from semsearch.core.types import FileKind, FileState, SearchHit, SegmentKind
from semsearch.core.vectors import DIM, l2_normalize
from semsearch.gui.desktop_providers import KRUNNER_MATCH_TYPE, GnomeSearchProvider, KRunnerProvider
from semsearch.services.indexer import IndexService
from semsearch.services.searcher import SearchService
from tests.testbed import generate_testbed


class _ConfigurableMockOllama(OllamaClient):
    """Offline OllamaClient supporting configurable vector dimension for integration testing."""

    def __init__(self, dim: int = DIM) -> None:
        super().__init__(base_url="http://127.0.0.1:11434")
        self.dim = dim

    def unload(self, model: str) -> None:
        del model

    def embed(
        self,
        model: str,
        inputs: list[str | dict[str, str]],
        *,
        truncate: bool = True,
        num_ctx: int | None = None,
    ) -> list[list[float]]:
        del model, truncate, num_ctx
        results: list[list[float]] = []
        for inp in inputs:
            seed = sum(ord(c) for c in str(inp)[:64]) % 100000
            rng = np.random.default_rng(seed)
            vec = l2_normalize(rng.standard_normal(self.dim).astype(np.float32))
            results.append([float(x) for x in vec])
        return results


class _FlakyOllama(_ConfigurableMockOllama):
    """OllamaClient that raises OllamaConnectionError until allow_connect is signaled."""

    def __init__(self) -> None:
        super().__init__(dim=DIM)
        self.allow_connect = threading.Event()
        self.failure_seen = threading.Event()
        self.failed_attempts = 0
        self._attempt_lock = threading.Lock()

    def embed(
        self,
        model: str,
        inputs: list[str | dict[str, str]],
        *,
        truncate: bool = True,
        num_ctx: int | None = None,
    ) -> list[list[float]]:
        if not self.allow_connect.is_set():
            with self._attempt_lock:
                self.failed_attempts += 1
            self.failure_seen.set()
            raise OllamaConnectionError("Simulated transient Ollama outage")
        return super().embed(model, inputs, truncate=truncate, num_ctx=num_ctx)


def _drain_pending(indexer: IndexService) -> None:
    while True:
        rec, target_db, _ = indexer._get_next_file()
        if rec is None or target_db is None:
            break
        assert indexer.process_file(rec, target_db) is True


def test_wal_concurrent_readers_and_indexer_writer_e2e(tmp_path: Path) -> None:
    """Verify real multi-thread WAL readers run concurrently with background IndexService writes without SQLITE_BUSY."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    doc_count = 20
    for i in range(doc_count):
        (docs_dir / f"report_{i:02d}.txt").write_text(
            f"Telemetry cluster report {i:02d} covering distributed consensus node NODE-{i:04d}.\n"
            f"Throughput metrics remained steady across shard {i % 4}.\n",
            encoding="utf-8",
        )

    with Database(tmp_path / "concurrent.db") as db, _ConfigurableMockOllama() as ollama:
        config = AppConfig(folders=[str(docs_dir)], code_enabled=False)
        indexer = IndexService(db=db, ollama=ollama, config=config)
        searcher = SearchService(db=db, ollama=ollama)

        indexer.crawl_and_sync(is_code=False)
        assert db.stats()["pending"] == doc_count

        stop_readers = threading.Event()
        errors: list[Exception] = []
        read_counts = [0, 0, 0, 0]

        def reader_worker(worker_idx: int) -> None:
            queries = ("Telemetry cluster report", "distributed consensus", f"NODE-{worker_idx:04d}")
            while not stop_readers.is_set():
                try:
                    q = queries[read_counts[worker_idx] % len(queries)]
                    searcher.search(q, limit=10)
                    db.stats()
                    db.get_vector_snapshot()
                    read_counts[worker_idx] += 1
                except Exception as exc:  # noqa: BLE001 — capture any concurrency or SQLITE_BUSY error
                    errors.append(exc)
                    return

        readers = [threading.Thread(target=reader_worker, args=(i,), daemon=True) for i in range(4)]
        for t in readers:
            t.start()

        indexer.start()
        try:
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if db.stats()["pending"] == 0:
                    break
                time.sleep(0.02)
        finally:
            stop_readers.set()
            for t in readers:
                t.join(timeout=3.0)
            indexer.stop()

        assert errors == []
        assert sum(read_counts) > 0
        final_stats = db.stats()
        assert final_stats["files"] == doc_count
        assert final_stats["pending"] == 0
        assert final_stats["failed"] == 0
        assert final_stats["vectors"] == doc_count

        res = searcher.search("NODE-0015")
        assert len(res.ranked) > 0
        assert Path(res.ranked[0].path).name == "report_15.txt"


def test_model_and_dimension_change_reindex_e2e(tmp_path: Path) -> None:
    """Verify switching embedding model/dimension (768 -> 512) cleanly resets and re-indexes without ValueError."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "alpha.txt").write_text("Alpha architecture blueprint and vector storage.", encoding="utf-8")
    (docs_dir / "beta.md").write_text("# Beta Release\nSemantic indexing pipeline specification.", encoding="utf-8")

    ollama = _ConfigurableMockOllama(dim=768)
    with Database(tmp_path / "model_switch.db") as db, ollama:
        config = AppConfig(folders=[str(docs_dir)], model="embed-model-768", code_enabled=False)
        indexer = IndexService(db=db, ollama=ollama, config=config)
        searcher = SearchService(db=db, ollama=ollama, model=config.model)

        indexer.crawl_and_sync(is_code=False)
        _drain_pending(indexer)

        assert db.get_meta("embedding_model") == "embed-model-768"
        assert db.get_meta("embedding_dim") == "768"
        snap_768 = db.get_vector_snapshot()
        assert snap_768.matrix.shape == (2, 768)
        assert len(searcher.search("Alpha architecture blueprint").ranked) > 0

        # Switch to a 512-dimensional model and synchronize metadata
        config.model = "embed-model-512"
        ollama.dim = 512
        searcher.model = config.model
        indexer.sync_model_state()

        assert db.stats()["pending"] == 2
        assert db.stats()["vectors"] == 0
        assert db.get_vector_snapshot().matrix.shape == (0, 0)

        # Re-index under the new 512-dim model and search cleanly
        _drain_pending(indexer)

        assert db.get_meta("embedding_model") == "embed-model-512"
        assert db.get_meta("embedding_dim") == "512"
        snap_512 = db.get_vector_snapshot()
        assert snap_512.matrix.shape == (2, 512)

        res_512 = searcher.search("Semantic indexing pipeline specification")
        assert len(res_512.ranked) > 0
        assert Path(res_512.ranked[0].path).name == "beta.md"


def test_transient_ollama_connection_error_recovery_e2e(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify transient Ollama outages pause waiting for Ollama and finish cleanly once reconnected."""
    monkeypatch.setattr(indexer_mod, "_INITIAL_OLLAMA_BACKOFF_SEC", 0.05)

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    for i in range(3):
        (docs_dir / f"memo_{i}.txt").write_text(f"Operational memo {i} for resilience recovery.", encoding="utf-8")

    ollama = _FlakyOllama()
    with Database(tmp_path / "resilience.db") as db, ollama:
        config = AppConfig(folders=[str(docs_dir)], code_enabled=False)
        indexer = IndexService(db=db, ollama=ollama, config=config)
        indexer.crawl_and_sync(is_code=False)

        indexer.start()
        try:
            assert ollama.failure_seen.wait(timeout=3.0) is True

            # Wait briefly for worker step to record waiting_for status
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and indexer.get_status().waiting_for != "Ollama":
                time.sleep(0.01)

            assert indexer.get_status().waiting_for == "Ollama"
            assert db.stats()["failed"] == 0
            assert db.stats()["pending"] == 3

            # Bring Ollama back online and verify full completion with zero FAILED files
            ollama.allow_connect.set()

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and db.stats()["pending"] > 0:
                time.sleep(0.02)

            assert db.stats()["pending"] == 0
            assert db.stats()["failed"] == 0
            assert db.stats()["vectors"] == 3
            assert indexer.get_status().waiting_for == ""
        finally:
            indexer.stop()


def test_svg_indexing_zero_ocr_and_binary_doc_skipped_e2e(tmp_path: Path) -> None:
    """Verify real .svg rasterizes via PyMuPDF as FileKind.IMAGE with Zero-OCR and legacy binary .doc is skipped."""
    testbed_dir = tmp_path / "testbed"
    files = generate_testbed(testbed_dir)
    svg_path = files["vector_logo"]

    legacy_doc = testbed_dir / "docs" / "legacy_memo.doc"
    legacy_doc.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1Legacy binary Word 97 document payload")

    assert classify_file(str(svg_path)) == FileKind.IMAGE
    assert classify_file(str(legacy_doc)) is None
    assert not hasattr(SegmentKind, "OCR")

    with Database(tmp_path / "svg_doc.db") as db, _ConfigurableMockOllama() as ollama:
        config = AppConfig(
            folders=[str(testbed_dir / "images"), str(testbed_dir / "docs")],
            code_enabled=False,
        )
        indexer = IndexService(db=db, ollama=ollama, config=config)
        indexer.crawl_and_sync(is_code=False)
        _drain_pending(indexer)

        svg_rec = db.find_by_path(str(svg_path))
        assert svg_rec is not None
        assert svg_rec.kind == FileKind.IMAGE
        assert svg_rec.state == FileState.DONE

        svg_segments = db.get_file_segments(svg_rec.id)
        assert len(svg_segments) == 1
        assert svg_segments[0][1] == SegmentKind.IMAGE

        has_vec = db._write_conn.execute(
            "SELECT has_vec FROM segments WHERE id = ?",
            (svg_segments[0][0],),
        ).fetchone()[0]
        assert has_vec == 1

        all_kinds = {row[0] for row in db._write_conn.execute("SELECT DISTINCT kind FROM segments").fetchall()}
        assert "ocr" not in all_kinds

        assert db.find_by_path(str(legacy_doc)) is None
        assert db.stats()["failed"] == 0


def _index_real_testbed(tmp_path: Path) -> tuple[ConfigManager, Path]:
    testbed_dir = tmp_path / "testbed"
    generate_testbed(testbed_dir)

    data_dir = tmp_path / "data"
    cfg_manager = ConfigManager(tmp_path / "config.json")
    cfg_manager.config.folders = [
        str(testbed_dir / "images"),
        str(testbed_dir / "pdfs"),
        str(testbed_dir / "docs"),
        str(testbed_dir / "media"),
    ]
    cfg_manager.config.code_folders = [str(testbed_dir / "code")]
    cfg_manager.config.code_enabled = True
    cfg_manager.save()

    ollama = _ConfigurableMockOllama()
    with Database(data_dir / MAIN_DB_FILE) as db, Database(data_dir / CODE_DB_FILE) as code_db, ollama:
        indexer = IndexService(db=db, code_db=code_db, ollama=ollama, config=cfg_manager.config)
        indexer.crawl_and_sync(is_code=False)
        indexer.crawl_and_sync(is_code=True)
        _drain_pending(indexer)

    return cfg_manager, data_dir


def test_cli_query_and_status_against_real_testbed_e2e(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify CLI `query --json` with inline kind filter and `status --json` against the real testbed DB."""
    cfg_manager, data_dir = _index_real_testbed(tmp_path)

    with _ConfigurableMockOllama() as ollama:
        rc_query = run_query(
            "kind:pdf Quarterly Systems Engineering",
            as_json=True,
            config_manager=cfg_manager,
            data_dir=data_dir,
            ollama_client=ollama,
        )
    assert rc_query == 0
    query_hits = json.loads(capsys.readouterr().out)
    assert len(query_hits) >= 1
    assert all(hit["kind"] == "pdf" for hit in query_hits)
    assert any(Path(hit["path"]).name == "digital_report.pdf" for hit in query_hits)

    rc_status = run_status(as_json=True, config_manager=cfg_manager, data_dir=data_dir)
    assert rc_status == 0
    status_data = json.loads(capsys.readouterr().out)
    assert status_data["main"]["files"] == 10
    assert status_data["main"]["pending"] == 0
    assert status_data["main"]["failed"] == 0
    assert status_data["main"]["vectors"] > 0
    assert status_data["main"]["embedding_model"] == "embeddinggemma-2"
    assert status_data["main"]["embedding_dim"] == DIM
    assert status_data["code"]["files"] == 2
    assert status_data["code"]["pending"] == 0


def test_dbus_providers_against_real_testbed_e2e(
    tmp_path: Path,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify KRunner and GNOME SearchProvider2 against the real indexed testbed DB."""
    del qapp
    cfg_manager, data_dir = _index_real_testbed(tmp_path)

    opened_paths: list[str] = []
    revealed_paths: list[str] = []

    def record_open(hit: SearchHit, _cfg: AppConfig | None = None) -> bool:
        opened_paths.append(hit.path)
        return True

    def record_reveal(path: str) -> bool:
        revealed_paths.append(path)
        return True

    monkeypatch.setattr("semsearch.gui.desktop_providers.execute_hit_action", record_open)
    monkeypatch.setattr("semsearch.gui.desktop_providers.reveal_in_file_manager", record_reveal)

    with (
        Database(data_dir / MAIN_DB_FILE) as db,
        Database(data_dir / CODE_DB_FILE) as code_db,
        _ConfigurableMockOllama() as ollama,
    ):
        searcher = SearchService(db=db, code_db=code_db, ollama=ollama, model=cfg_manager.config.model)

        krunner = KRunnerProvider(searcher, cfg_manager.config)
        assert krunner.actions_payload()[0][0] == "reveal"

        krunner_matches = krunner.match_query("Executive Strategy Brief")
        assert len(krunner_matches) > 0
        match_id, text, icon, match_type, relevance, props = krunner_matches[0]
        assert Path(match_id).name == "brief.docx"
        assert text == "brief.docx"
        assert icon == "x-office-document"
        assert match_type == KRUNNER_MATCH_TYPE
        assert 0.0 < relevance <= 1.0
        assert "subtext" in props
        assert "category" in props

        assert krunner.run_match(match_id, "") is True
        assert krunner.run_match(match_id, "reveal") is True
        assert opened_paths[-1] == match_id
        assert revealed_paths[-1] == match_id

        gnome = GnomeSearchProvider(searcher, cfg_manager.config)
        launched_queries: list[str] = []
        gnome.launch_panel_requested.connect(launched_queries.append)

        gnome_ids = gnome.get_result_ids(["Executive", "Strategy", "Brief"])
        assert len(gnome_ids) > 0
        assert Path(gnome_ids[0]).name == "brief.docx"

        metas = gnome.get_result_metas(gnome_ids[:1])
        assert len(metas) == 1
        assert metas[0]["id"] == gnome_ids[0]
        assert metas[0]["name"] == "brief.docx"
        assert metas[0]["gicon"] == "x-office-document"

        assert gnome.activate_result(gnome_ids[0]) is True
        assert opened_paths[-1] == gnome_ids[0]

        gnome.launch_search(["kind:pdf", "Quarterly"])
        assert launched_queries == ["kind:pdf Quarterly"]
