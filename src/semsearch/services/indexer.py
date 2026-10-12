"""Background indexing service, multi-tier priority scheduling, and pipelined execution."""

from dataclasses import dataclass, field
from enum import Enum
import os
from pathlib import Path
import threading
import time
import numpy as np

from semsearch.config import AppConfig
from semsearch.core.crawler import crawl_folder, sync_candidates
from semsearch.core.database import Database
from semsearch.core.ollama import OllamaClient
from semsearch.core.rules import classify_file
from semsearch.core.types import (
    Candidate,
    EngineActivity,
    EngineStatus,
    ExtractedSegment,
    FileKind,
    FileRecord,
    FileState,
    Modality,
)
from semsearch.core.vectors import float16_to_bytes, l2_normalize
from semsearch.extractors.code import extract_code_segments, extract_notebook_segments
from semsearch.extractors.docs import extract_doc_segments
from semsearch.extractors.images import extract_image_segment
from semsearch.extractors.media import extract_audio_segments, extract_video_segments
from semsearch.extractors.pdfs import extract_pdf


class IndexQueueTier(str, Enum):
    """Priority queue tiers for file indexing."""

    NEW_FILES = "new_files"
    BACKFILL = "backfill"
    CODE = "code"
    LONG_FILES = "long_files"


@dataclass
class MeasuredCosts:
    """Tracks running throughput metrics, remaining time, and database storage growth."""

    ms_per_chunk: float = 40.0
    ms_per_image: float = 120.0
    ms_per_audio: float = 150.0
    bytes_per_segment: int = 1600

    _counts: dict[str, int] = field(default_factory=lambda: {"text": 0, "image": 0, "audio": 0})
    _total_ms: dict[str, float] = field(default_factory=lambda: {"text": 0.0, "image": 0.0, "audio": 0.0})

    def update(self, modality: Modality, elapsed_ms: float, count: int = 1, bytes_added: int = 0) -> None:
        """Update moving average processing time and segment byte size."""
        if count <= 0:
            return
        key = "text"
        if modality == Modality.IMAGE:
            key = "image"
        elif modality == Modality.AUDIO:
            key = "audio"

        self._counts[key] += count
        self._total_ms[key] += elapsed_ms

        if key == "text":
            self.ms_per_chunk = self._total_ms["text"] / self._counts["text"]
        elif key == "image":
            self.ms_per_image = self._total_ms["image"] / self._counts["image"]
        elif key == "audio":
            self.ms_per_audio = self._total_ms["audio"] / self._counts["audio"]

        if bytes_added > 0:
            self.bytes_per_segment = max(int(bytes_added / count), 100)

    def estimate_time(self, pending_counts: dict[str, int]) -> float:
        """Estimate remaining duration in seconds based on pending items per kind."""
        total_ms = 0.0
        for kind_name, count in pending_counts.items():
            if kind_name in ("image", "screenshot"):
                total_ms += count * self.ms_per_image
            elif kind_name in ("audio", "video"):
                total_ms += count * self.ms_per_audio * 5.0
            elif kind_name == "pdf":
                total_ms += count * (10.0 * self.ms_per_chunk + 2.0 * self.ms_per_image)
            else:
                total_ms += count * (3.0 * self.ms_per_chunk)
        return total_ms / 1000.0

    def estimate_disk_growth(self, pending_count: int) -> int:
        """Estimate required database disk space in bytes for pending items."""
        return pending_count * 3 * self.bytes_per_segment


class IndexService:
    """Orchestrates background file indexing, priority queues, and pipelined extraction."""

    def __init__(
        self,
        db: Database,
        code_db: Database | None = None,
        ollama: OllamaClient | None = None,
        config: AppConfig | None = None,
    ) -> None:
        self.db = db
        self.code_db = code_db
        self.ollama = ollama
        self.config = config or AppConfig()

        self.costs = MeasuredCosts()
        self.status = EngineStatus(activity=EngineActivity.STARTING)

        self._new_files_queue: list[int] = []
        self._lock = threading.Lock()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None

    def start(self) -> None:
        """Launch background worker thread."""
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._pause_event.set()
        self.status.activity = EngineActivity.STARTING
        self._worker_thread = threading.Thread(target=self._worker_loop, name="IndexerWorkerThread", daemon=True)
        self._worker_thread.start()

    def stop(self) -> None:
        """Stop background worker thread."""
        self._stop_event.set()
        self._pause_event.set()
        if self._worker_thread is not None and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
        self._worker_thread = None

    def pause(self, reason: str = "") -> None:
        """Pause indexing operations."""
        self._pause_event.clear()
        with self._lock:
            self.status.paused = True
            self.status.waiting_for = reason
            self.status.activity = EngineActivity.PAUSED

    def resume(self) -> None:
        """Resume indexing operations."""
        with self._lock:
            self.status.paused = False
            self.status.waiting_for = ""
            self.status.activity = EngineActivity.INDEXING
        self._pause_event.set()

    def enqueue_file(self, path: str, tier: IndexQueueTier = IndexQueueTier.NEW_FILES) -> None:
        """Enqueue newly modified or created file for immediate or backfill indexing."""
        resolved = str(Path(path).resolve())
        is_code = False
        if self.config.code_enabled and self.code_db is not None:
            is_code = any(resolved.startswith(str(Path(cf).resolve())) for cf in self.config.code_folders)

        target_db = self.code_db if (is_code and self.code_db) else self.db
        rec = target_db.find_by_path(resolved)

        if rec is None:
            if not os.path.exists(resolved):
                return
            kind = classify_file(resolved)
            if kind is None:
                return
            try:
                st = os.stat(resolved)
                cand = Candidate(
                    path=resolved,
                    kind=kind,
                    size=st.st_size,
                    mtime=st.st_mtime,
                    inode=st.st_ino,
                    device=st.st_dev,
                )
                fid = target_db.insert_file(cand)
            except OSError:
                return
        else:
            fid = rec.id
            if not os.path.exists(resolved):
                return
            try:
                st = os.stat(resolved)
                if rec.state != FileState.PENDING or st.st_mtime != rec.mtime or st.st_size != rec.size:
                    kind = classify_file(resolved) or rec.kind
                    cand = Candidate(
                        path=resolved,
                        kind=kind,
                        size=st.st_size,
                        mtime=st.st_mtime,
                        inode=st.st_ino,
                        device=st.st_dev,
                    )
                    target_db.requeue_file(rec.id, cand)
            except OSError:
                return

        with self._lock:
            if tier == IndexQueueTier.NEW_FILES and len(self._new_files_queue) < 50:
                if fid not in self._new_files_queue:
                    self._new_files_queue.append(fid)

    def crawl_and_sync(self, roots: list[str] | None = None, is_code: bool = False) -> None:
        """Crawl folders and synchronize database records."""
        with self._lock:
            self.status.activity = EngineActivity.SYNCING

        target_db = self.code_db if (is_code and self.code_db) else self.db
        scan_roots = roots or (self.config.code_folders if is_code else self.config.folders)

        for root in scan_roots:
            candidates = crawl_folder(root, is_code=is_code)
            sync_candidates(target_db, candidates, roots=root)

    def _get_next_file(self) -> tuple[FileRecord | None, Database | None, IndexQueueTier | None]:
        with self._lock:
            # Tier 1: Immediate New Files (<= 50)
            while self._new_files_queue:
                fid = self._new_files_queue.pop(0)
                rec = self.db.find_by_id(fid)
                if rec and rec.state == FileState.PENDING:
                    return rec, self.db, IndexQueueTier.NEW_FILES
                if self.code_db:
                    rec = self.code_db.find_by_id(fid)
                    if rec and rec.state == FileState.PENDING:
                        return rec, self.code_db, IndexQueueTier.NEW_FILES

        # Tier 2: General backfill
        pending_general = self.db.list_pending(limit=1)
        if pending_general:
            return pending_general[0], self.db, IndexQueueTier.BACKFILL

        # Tier 3: Code backfill
        if self.config.code_enabled and self.code_db is not None:
            pending_code = self.code_db.list_pending(limit=1)
            if pending_code:
                return pending_code[0], self.code_db, IndexQueueTier.CODE

        # Tier 4: Long files completion
        partial_gen = self._find_partial_file(self.db)
        if partial_gen:
            return partial_gen, self.db, IndexQueueTier.LONG_FILES
        if self.config.code_enabled and self.code_db is not None:
            partial_code = self._find_partial_file(self.code_db)
            if partial_code:
                return partial_code, self.code_db, IndexQueueTier.LONG_FILES

        return None, None, None

    def _find_partial_file(self, db: Database) -> FileRecord | None:
        cursor = db._conn.cursor()
        cursor.execute(
            """
            SELECT id, path, kind, size, mtime, inode, device, state, detail, info, indexed_at
            FROM files WHERE state = 'partial'
            ORDER BY mtime DESC LIMIT 1
            """
        )
        row = cursor.fetchone()
        return db._row_to_file_record(row) if row else None

    def _extract_segments(
        self,
        file_record: FileRecord,
        start_offset: int = 0,
    ) -> tuple[list[ExtractedSegment], bool, dict]:
        path = file_record.path
        kind = file_record.kind
        is_complete = True
        info = dict(file_record.info)

        if kind == FileKind.IMAGE:
            return [extract_image_segment(path, is_screenshot=False)], True, info
        if kind == FileKind.SCREENSHOT:
            return [extract_image_segment(path, is_screenshot=True)], True, info
        if kind == FileKind.PDF:
            segments, complete = extract_pdf(path, start_page=start_offset, max_pages=30)
            last_page = start_offset + len(segments)
            info["last_page"] = last_page
            return segments, complete, info
        if kind == FileKind.DOC:
            all_segments = extract_doc_segments(path)
            if len(all_segments) > 40:
                end_chunk = min(start_offset + 40, len(all_segments))
                batch = all_segments[start_offset:end_chunk]
                complete = end_chunk >= len(all_segments)
                info["last_chunk"] = end_chunk
                return batch, complete, info
            return all_segments, True, info
        if kind == FileKind.AUDIO:
            return extract_audio_segments(path), True, info
        if kind == FileKind.VIDEO:
            return extract_video_segments(path), True, info
        if kind == FileKind.CODE:
            if path.endswith(".ipynb"):
                return extract_notebook_segments(path), True, info
            return extract_code_segments(path), True, info

        return [], True, info

    def process_file(self, file_record: FileRecord, target_db: Database) -> bool:
        """Execute pipelined extraction, Ollama embedding, and database commit for a file."""
        if not os.path.exists(file_record.path):
            target_db.delete_file(file_record.id)
            return False

        t0 = time.time()
        start_offset = 0
        if file_record.state == FileState.PARTIAL:
            if file_record.kind == FileKind.PDF:
                start_offset = file_record.info.get("last_page", 0)
            elif file_record.kind == FileKind.DOC:
                start_offset = file_record.info.get("last_chunk", 0)

        # Stage 1: Preparation & Model Inference
        try:
            segments, is_complete, info = self._extract_segments(file_record, start_offset=start_offset)
        except Exception as exc:
            target_db.update_file_state(file_record.id, FileState.FAILED, detail=str(exc))
            return False

        vectors: list[bytes | None] = []
        if self.ollama is not None and segments:
            embed_inputs: list[str | dict[str, str]] = []
            for s in segments:
                if s.modality != Modality.NONE and s.payload:
                    embed_inputs.append(s.payload)
                else:
                    embed_inputs.append("")

            try:
                embeddings = self.ollama.embed(self.config.model, embed_inputs)
            except Exception:
                embeddings = []

            for idx, s in enumerate(segments):
                if idx < len(embeddings) and embeddings[idx]:
                    vec_arr = l2_normalize(np.array(embeddings[idx], dtype=np.float32))
                    vectors.append(float16_to_bytes(vec_arr))
                else:
                    vectors.append(None)
        else:
            vectors = [None] * len(segments)

        # Stage 2: Database Persistence
        bytes_added = 0
        for s, vec in zip(segments, vectors):
            name = Path(file_record.path).name
            body = s.payload if isinstance(s.payload, str) and s.payload else s.excerpt
            orig = s.payload if isinstance(s.payload, str) else ""
            target_db.insert_segment(
                file_id=file_record.id,
                kind=s.kind,
                modality=s.modality,
                loc=s.loc,
                loc_end=s.loc_end,
                excerpt=s.excerpt,
                vec=vec,
                name=name,
                body=body,
                original=orig,
            )
            bytes_added += len(vec) if vec else 0

        new_state = FileState.DONE if is_complete else FileState.PARTIAL
        target_db.update_file_state(file_record.id, new_state, info=info)

        elapsed_ms = (time.time() - t0) * 1000.0
        if segments:
            self.costs.update(segments[0].modality, elapsed_ms, count=len(segments), bytes_added=bytes_added)

        return True

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            self._pause_event.wait()
            if self._stop_event.is_set():
                break

            file_rec, target_db, tier = self._get_next_file()
            if file_rec is None or target_db is None:
                with self._lock:
                    if not self.status.paused:
                        self.status.activity = EngineActivity.IDLE
                        self.status.current_file = ""
                time.sleep(0.2)
                continue

            with self._lock:
                self.status.activity = EngineActivity.FINISHING if tier == IndexQueueTier.LONG_FILES else EngineActivity.INDEXING
                self.status.current_file = Path(file_rec.path).name

            try:
                self.process_file(file_rec, target_db)
            except Exception:
                pass
            self._update_status_counts()

    def _update_status_counts(self) -> None:
        gen_stats = self.db.stats()
        code_stats = self.code_db.stats() if self.code_db else {"files": 0, "pending": 0, "vectors": 0}

        total_files = gen_stats["files"] + code_stats["files"]
        pending_files = gen_stats["pending"] + code_stats["pending"]
        vectors = gen_stats["vectors"] + code_stats["vectors"]
        indexed = max(total_files - pending_files, 0)

        with self._lock:
            self.status.files = total_files
            self.status.pending = pending_files
            self.status.indexed = indexed
            self.status.total = total_files
            self.status.vectors = vectors
            self.status.searchable = total_files

    def get_status(self) -> EngineStatus:
        """Return the current engine operational state and counters."""
        self._update_status_counts()
        with self._lock:
            return EngineStatus(
                activity=self.status.activity,
                pending=self.status.pending,
                indexed=self.status.indexed,
                total=self.status.total,
                searchable=self.status.searchable,
                files=self.status.files,
                vectors=self.status.vectors,
                paused=self.status.paused,
                waiting_for=self.status.waiting_for,
                message=self.status.message,
                current_file=self.status.current_file,
            )
