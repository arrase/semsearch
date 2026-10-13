"""End-to-end multimodal integration, search validation, action deep-linking, and invariant verification."""

import ast
import base64
import io
from pathlib import Path
import subprocess
from unittest.mock import MagicMock
import numpy as np
from PIL import Image
import pytest

from semsearch.config import AppConfig
from semsearch.core.database import Database
from semsearch.core.search_text import is_lookup_query
from semsearch.core.types import (
    Candidate,
    FileKind,
    FileState,
    Modality,
    SearchHit,
    SegmentKind,
)
from semsearch.core.vectors import DIM, l2_normalize
from semsearch.main import build_parser
from semsearch.services.actions import (
    execute_hit_action,
    reveal_in_file_manager,
)
from semsearch.services.indexer import IndexService
from semsearch.services.searcher import ResultSet, SearchService
from tests.testbed import generate_testbed

BANNED_OCR_LIBRARIES = {
    "pytesseract",
    "tesseract",
    "easyocr",
    "paddleocr",
    "ocrmypdf",
    "tesserocr",
}


class E2ESemanticMockOllama:
    """Deterministic embedding provider for multimodal search evaluation."""

    def __init__(self) -> None:
        rng = np.random.default_rng(2026)
        self.sunset_vec = l2_normalize(rng.standard_normal(DIM).astype(np.float32))
        self.video_vec = l2_normalize(rng.standard_normal(DIM).astype(np.float32))
        self.speech_vec = l2_normalize(rng.standard_normal(DIM).astype(np.float32))
        self.video_audio_vec = l2_normalize(rng.standard_normal(DIM).astype(np.float32))
        self.brief_vec = l2_normalize(rng.standard_normal(DIM).astype(np.float32))
        self.code_vec = l2_normalize(rng.standard_normal(DIM).astype(np.float32))
        self.default_vec = l2_normalize(rng.standard_normal(DIM).astype(np.float32))

    def embed(self, model: str, inputs: list[str | dict[str, str]]) -> list[list[float]]:
        results: list[list[float]] = []
        for inp in inputs:
            if isinstance(inp, dict):
                if "image" in inp:
                    raw = base64.b64decode(inp["image"])
                    with Image.open(io.BytesIO(raw)) as im:
                        if im.size == (640, 480):
                            results.append(self.sunset_vec.tolist())
                        elif im.size == (320, 240):
                            results.append(self.video_vec.tolist())
                        else:
                            results.append(self.default_vec.tolist())
                elif "audio" in inp:
                    raw = base64.b64decode(inp["audio"])
                    if len(raw) > 500000:
                        results.append(self.speech_vec.tolist())
                    else:
                        results.append(self.video_audio_vec.tolist())
                else:
                    results.append(self.default_vec.tolist())
            else:
                text = str(inp).lower()
                clean = (
                    text.replace("task: search result | query: ", "")
                    .replace("task: code retrieval | query: ", "")
                    .strip()
                )
                if any(w in clean for w in ("sunset", "golden", "landscape")):
                    results.append(self.sunset_vec.tolist())
                elif any(w in clean for w in ("video", "smpte", "bars")):
                    results.append(self.video_vec.tolist())
                elif any(w in clean for w in ("speech", "voice", "tone")):
                    results.append(self.speech_vec.tolist())
                elif any(w in clean for w in ("brief", "strategy", "executive")):
                    results.append(self.brief_vec.tolist())
                elif any(w in clean for w in ("retry", "backoff", "token", "bucket")):
                    results.append(self.code_vec.tolist())
                else:
                    results.append(self.default_vec.tolist())
        return results


def _build_and_index_testbed(
    tmp_path: Path,
) -> tuple[Database, Database, SearchService, dict[str, Path]]:
    testbed_dir = tmp_path / "testbed"
    files = generate_testbed(testbed_dir)

    db = Database(tmp_path / "semsearch.db")
    code_db = Database(tmp_path / "code.db")

    config = AppConfig(
        folders=[
            str(testbed_dir / "images"),
            str(testbed_dir / "pdfs"),
            str(testbed_dir / "docs"),
            str(testbed_dir / "media"),
        ],
        code_folders=[str(testbed_dir / "code")],
        code_enabled=True,
    )

    ollama = E2ESemanticMockOllama()
    indexer = IndexService(db=db, code_db=code_db, ollama=ollama, config=config)

    indexer.crawl_and_sync(is_code=False)
    indexer.crawl_and_sync(is_code=True)

    while True:
        rec, target_db, _ = indexer._get_next_file()
        if rec is None or target_db is None:
            break
        indexer.process_file(rec, target_db)

    searcher = SearchService(db=db, code_db=code_db, ollama=ollama)
    return db, code_db, searcher, files


def test_testbed_indexing_e2e(tmp_path: Path) -> None:
    db, code_db, _, files = _build_and_index_testbed(tmp_path)

    # All files indexed
    assert db.stats()["files"] == 9
    assert db.stats()["pending"] == 0
    assert code_db.stats()["files"] == 2
    assert code_db.stats()["pending"] == 0

    # Segments existence and FTS indexing
    cursor = db._conn.cursor()
    cursor.execute("SELECT count(*) FROM segments")
    total_segments = cursor.fetchone()[0]
    assert total_segments >= 12

    cursor.execute("SELECT count(*) FROM fts")
    fts_rows = cursor.fetchone()[0]
    assert fts_rows > 0

    # Ensure zero failed files
    cursor.execute("SELECT count(*) FROM files WHERE state = 'failed'")
    assert cursor.fetchone()[0] == 0


def test_zero_ocr_invariant() -> None:
    src_dir = Path(__file__).resolve().parent.parent / "src" / "semsearch"
    assert src_dir.is_dir()

    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name_root = alias.name.split(".")[0].lower()
                    assert name_root not in BANNED_OCR_LIBRARIES, (
                        f"Banned OCR library {alias.name} imported in {py_file}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    mod_root = node.module.split(".")[0].lower()
                    assert mod_root not in BANNED_OCR_LIBRARIES, (
                        f"Banned OCR module {node.module} imported in {py_file}"
                    )

        lower_content = content.lower()
        for banned in BANNED_OCR_LIBRARIES:
            assert banned not in lower_content, (
                f"Banned OCR keyword {banned} found in {py_file}"
            )


def test_cross_modal_and_keyword_search_e2e(tmp_path: Path) -> None:
    db, code_db, searcher, files = _build_and_index_testbed(tmp_path)

    # 1. Text-to-Image visual query
    res_img = searcher.search("photo sunset golden landscape")
    assert len(res_img.ranked) > 0
    assert Path(res_img.ranked[0].path).name == "photo_sunset.jpg"

    # 2. Text-to-Video moment query
    res_vid = searcher.search("video smpte color bars test")
    assert len(res_vid.ranked) > 0
    vid_hits = [h for h in res_vid.ranked if Path(h.path).name == "clip_video.mp4"]
    assert len(vid_hits) > 0
    assert vid_hits[0].segment in (SegmentKind.FRAME, SegmentKind.AUDIO)

    # 3. Document text query
    res_doc = searcher.search("Executive Strategy Brief")
    assert len(res_doc.ranked) > 0
    assert Path(res_doc.ranked[0].path).name == "brief.docx"

    # 4. Audio tone query
    res_audio = searcher.search("speech audio tone recording")
    assert len(res_audio.ranked) > 0
    audio_hits = [h for h in res_audio.ranked if Path(h.path).name == "speech_audio.wav"]
    assert len(audio_hits) > 0


def test_code_search_and_definition_anchor_e2e(tmp_path: Path) -> None:
    db, code_db, searcher, files = _build_and_index_testbed(tmp_path)

    # Function search with definition line anchor
    res_fn = searcher.search("code: retry_with_backoff")
    assert len(res_fn.ranked) > 0
    top_fn = res_fn.ranked[0]
    assert Path(top_fn.path).name == "service.py"
    assert top_fn.focus == 6.0

    # Class search with definition line anchor
    res_cls = searcher.search("code: TokenBucket")
    assert len(res_cls.ranked) > 0
    top_cls = res_cls.ranked[0]
    assert Path(top_cls.path).name == "service.py"
    assert top_cls.focus == 13.0

    # Jupyter notebook stripped code search
    res_nb = searcher.search("code: calculate_metrics")
    assert len(res_nb.ranked) > 0
    nb_hits = [h for h in res_nb.ranked if Path(h.path).name == "analysis.ipynb"]
    assert len(nb_hits) > 0
    assert nb_hits[0].loc > 0


def test_lookup_serial_query_e2e(tmp_path: Path) -> None:
    db, code_db, searcher, files = _build_and_index_testbed(tmp_path)

    # Serial query triggers lookup detection
    assert is_lookup_query("ERR-4096-FAIL") is True
    assert is_lookup_query("INV-2026-X99") is True

    # Hardware serial BM25 lookup
    res_serial = searcher.search("ERR-4096-FAIL")
    assert len(res_serial.ranked) > 0
    assert Path(res_serial.ranked[0].path).name == "notes.txt"
    assert "4096" in res_serial.ranked[0].excerpt

    # Account reference lookup
    res_inv = searcher.search("INV-2026-X99")
    assert len(res_inv.ranked) > 0
    assert Path(res_inv.ranked[0].path).name == "notes.txt"


def test_weaker_matches_folding_e2e(tmp_path: Path) -> None:
    db, code_db, searcher, files = _build_and_index_testbed(tmp_path)

    cand = Candidate(path="/tmp/weak_doc.txt", kind=FileKind.DOC, size=50, mtime=1.0, inode=888, device=888)
    fid = db.insert_file(cand)
    db.insert_segment(fid, SegmentKind.CHUNK, Modality.TEXT, 1.0, 1.0, "Executive Strategy overview report", None, "weak_doc.txt", "Executive Strategy overview report", "")

    res = searcher.search("Executive Strategy Brief client deployment")
    assert isinstance(res, ResultSet)
    assert len(res.ranked) >= 1

    top_score = res.ranked[0].score
    for weaker_hit in res.weaker:
        assert weaker_hit.score < 0.5 * top_score


def test_desktop_actions_e2e(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[list[str]] = []

    def mock_popen(args: list[str], **kwargs) -> None:
        launched.append(args)

    monkeypatch.setattr(subprocess, "Popen", mock_popen)

    # 1. Code editor with line target
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "code" else None)
    hit_code = SearchHit(path="/src/worker.py", kind=FileKind.CODE, score=1.0, focus=15.0)
    assert execute_hit_action(hit_code) is True
    assert launched[-1] == ["code", "-g", "/src/worker.py:15"]

    # 2. PDF document with page target
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "okular" else None)
    hit_pdf = SearchHit(path="/docs/paper.pdf", kind=FileKind.PDF, score=1.0, loc=3.0)
    assert execute_hit_action(hit_pdf) is True
    assert launched[-1] == ["okular", "-p", "3", "/docs/paper.pdf"]

    # 3. Media player with timestamp target
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "mpv" else None)
    hit_media = SearchHit(path="/media/sound.wav", kind=FileKind.AUDIO, score=1.0, loc=20.0)
    assert execute_hit_action(hit_media) is True
    assert launched[-1] == ["mpv", "--start=20", "/media/sound.wav"]

    # 4. Fallback default handler
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    hit_doc = SearchHit(path="/docs/readme.txt", kind=FileKind.DOC, score=1.0)
    assert execute_hit_action(hit_doc) is True
    assert launched[-1] == ["xdg-open", "/docs/readme.txt"]

    # 5. File manager reveal
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "dolphin" else None)
    assert reveal_in_file_manager("/docs/readme.txt") is True
    assert launched[-1] == ["dolphin", "--select", str(Path("/docs/readme.txt").resolve())]


def test_cli_flags_e2e() -> None:
    parser = build_parser()

    # --help validation
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--help"])
    assert exc_info.value.code == 0

    # Flags validation
    assert parser.parse_args(["--panel"]).panel is True
    assert parser.parse_args(["--window"]).window is True
    assert parser.parse_args(["--settings"]).settings is True
    assert parser.parse_args(["--daemon"]).daemon is True
