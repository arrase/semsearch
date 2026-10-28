"""Background indexing service, multi-tier priority scheduling, and pipelined execution."""

import threading
from enum import StrEnum
from pathlib import Path

import numpy as np

from semsearch.config import AppConfig
from semsearch.core.crawler import crawl_folder, sync_candidates
from semsearch.core.database import Database
from semsearch.core.ollama import OllamaClient, format_doc_prompt
from semsearch.core.rules import classify_file, is_code_project_root, is_path_excluded
from semsearch.core.search_text import has_words
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
from semsearch.extractors.code import extract_code, extract_notebook
from semsearch.extractors.docs import extract_doc
from semsearch.extractors.images import extract_image
from semsearch.extractors.media import extract_audio, extract_video
from semsearch.extractors.pdfs import extract_pdf


class IndexQueueTier(StrEnum):
    """Priority queue tiers for file indexing."""

    NEW_FILES = "new_files"
    BACKFILL = "backfill"
    CODE = "code"
    LONG_FILES = "long_files"


def _code_place(path: str, code_folders: list[str] | None = None) -> tuple[str | None, str]:
    """Determine repo and title path for code files matching project root markers first."""
    path_obj = Path(path).resolve()
    home = Path.home().resolve()
    folder = path_obj.parent
    repo: Path | None = None

    curr = folder
    while curr not in (curr.parent, home):
        if (curr / ".git").exists() or is_code_project_root(str(curr)):
            repo = curr
            break
        curr = curr.parent

    if repo is None and code_folders:
        for cf in code_folders:
            cf_path = Path(cf).resolve()
            if path_obj.is_relative_to(cf_path):
                repo = cf_path
                break

    if repo is not None:
        try:
            return str(repo), str(path_obj.relative_to(repo))
        except ValueError:
            return str(repo), path_obj.name

    parts = path_obj.parts
    title = "/".join(parts[-2:]) if len(parts) >= 2 else path_obj.name
    return None, title


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

        self.status = EngineStatus(activity=EngineActivity.STARTING)

        self._new_files_queue: list[tuple[int, bool]] = []
        self._lock = threading.Lock()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._work_event = threading.Event()
        self._work_event.set()
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None

    def start(self) -> None:
        """Launch background worker thread."""
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._pause_event.set()
        self._work_event.set()
        self.status.activity = EngineActivity.STARTING
        self._worker_thread = threading.Thread(target=self._worker_loop, name="IndexerWorkerThread", daemon=True)
        self._worker_thread.start()

    def stop(self) -> None:
        """Stop background worker thread."""
        self._stop_event.set()
        self._pause_event.set()
        self._work_event.set()
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
        self._work_event.set()

    def _is_code_file_path(self, resolved_path: Path) -> bool:
        if not (self.config.code_enabled and self.code_db is not None):
            return False
        return any(resolved_path.is_relative_to(Path(cf).resolve()) for cf in self.config.code_folders)

    def _is_excluded_general_ext(self, ext: str, is_code: bool) -> bool:
        if is_code or not self.config.excluded_types:
            return False
        norm_excl = {t.lstrip(".").strip().lower() for t in self.config.excluded_types if t.strip()}
        return ext in norm_excl

    @staticmethod
    def _upsert_candidate(target_db: Database, rec: FileRecord | None, cand: Candidate) -> int:
        if rec is None:
            return target_db.insert_file(cand)
        if rec.state != FileState.PENDING or cand.mtime != rec.mtime or cand.size != rec.size:
            target_db.requeue_file(rec.id, cand)
        return rec.id

    def enqueue_file(self, path: str, tier: IndexQueueTier = IndexQueueTier.NEW_FILES) -> None:
        """Enqueue newly modified or created file for immediate or backfill indexing."""
        resolved_path = Path(path).resolve()
        resolved = str(resolved_path)
        if not resolved_path.exists() or is_path_excluded(resolved, self.config.excluded_folders):
            return

        is_code = self._is_code_file_path(resolved_path)
        ext = resolved_path.suffix.lstrip(".").lower()
        if self._is_excluded_general_ext(ext, is_code):
            return

        target_db = self.code_db if is_code and self.code_db is not None else self.db
        rec = target_db.find_by_path(resolved)

        try:
            st = resolved_path.stat()
        except OSError:
            return

        kind = classify_file(resolved, is_code_mode=is_code) or (rec.kind if rec else None)
        if kind is None:
            return

        cand = Candidate(
            path=resolved,
            kind=kind,
            size=st.st_size,
            mtime=st.st_mtime,
            inode=st.st_ino,
            device=st.st_dev,
        )
        fid = self._upsert_candidate(target_db, rec, cand)

        with self._lock:
            if tier == IndexQueueTier.NEW_FILES and len(self._new_files_queue) < 50:
                item = (fid, is_code)
                if item not in self._new_files_queue:
                    self._new_files_queue.append(item)

        self._update_status_counts()
        self._work_event.set()

    def _crawl_single_root(self, root: str, is_code: bool, excl_types: list[str] | None) -> list[Candidate]:
        if self.config.excluded_folders or (excl_types and excl_types != ["heic"]):
            return crawl_folder(
                root,
                is_code=is_code,
                excluded_folders=self.config.excluded_folders,
                excluded_types=excl_types,
            )
        return crawl_folder(root, is_code=is_code)

    def crawl_and_sync(self, roots: list[str] | None = None, is_code: bool = False) -> None:
        """Crawl folders and synchronize database records."""
        if self._stop_event.is_set() or (is_code and not self.code_db):
            return

        with self._lock:
            if not self.status.paused and self.status.activity in (EngineActivity.STARTING, EngineActivity.IDLE):
                self.status.activity = EngineActivity.SYNCING

        target_db = self.code_db if is_code and self.code_db is not None else self.db
        scan_roots = roots or (self.config.code_folders if is_code else self.config.folders)
        excl_types = None if is_code else self.config.excluded_types

        for root in scan_roots:
            if self._stop_event.is_set():
                break
            candidates = self._crawl_single_root(root, is_code, excl_types)
            sync_candidates(target_db, candidates, roots=[root])

        self._update_status_counts()
        with self._lock:
            if self.status.paused:
                self.status.activity = EngineActivity.PAUSED
            elif self.status.activity == EngineActivity.SYNCING:
                self.status.activity = EngineActivity.IDLE
        self._work_event.set()

    def _pop_next_queued_file(self) -> tuple[FileRecord, Database] | None:
        with self._lock:
            while self._new_files_queue:
                fid, is_code = self._new_files_queue.pop(0)
                target_db = self.code_db if is_code else self.db
                if target_db is None:
                    continue
                rec = target_db.find_by_id(fid)
                if rec and rec.state == FileState.PENDING:
                    return rec, target_db
        return None

    def _get_next_file(self) -> tuple[FileRecord | None, Database | None, IndexQueueTier | None]:
        queued = self._pop_next_queued_file()
        if queued is not None:
            return queued[0], queued[1], IndexQueueTier.NEW_FILES

        pending_general = self.db.list_pending(limit=1)
        if pending_general:
            return pending_general[0], self.db, IndexQueueTier.BACKFILL

        if self.config.code_enabled and self.code_db is not None:
            pending_code = self.code_db.list_pending(limit=1)
            if pending_code:
                return pending_code[0], self.code_db, IndexQueueTier.CODE

        partial_gen = self.db.find_partial()
        if partial_gen:
            return partial_gen, self.db, IndexQueueTier.LONG_FILES
        if self.config.code_enabled and self.code_db is not None:
            partial_code = self.code_db.find_partial()
            if partial_code:
                return partial_code, self.code_db, IndexQueueTier.LONG_FILES

        return None, None, None

    def _extract_segments(
        self,
        file_record: FileRecord,
        start_offset: int = 0,
    ) -> tuple[list[ExtractedSegment], bool, dict]:
        path = file_record.path
        kind = file_record.kind
        info = dict(file_record.info)

        if kind == FileKind.IMAGE:
            return [extract_image(path, is_screenshot=False)], True, info
        if kind == FileKind.SCREENSHOT:
            return [extract_image(path, is_screenshot=True)], True, info
        if kind == FileKind.PDF:
            segments, complete = extract_pdf(path, start_page=start_offset, max_pages=30)
            info["last_page"] = start_offset + len(segments)
            return segments, complete, info
        if kind == FileKind.DOC:
            all_segments = extract_doc(path)
            if len(all_segments) > 40:
                end_chunk = min(start_offset + 40, len(all_segments))
                batch = all_segments[start_offset:end_chunk]
                info["last_chunk"] = end_chunk
                return batch, end_chunk >= len(all_segments), info
            return all_segments, True, info
        if kind == FileKind.AUDIO:
            return extract_audio(path), True, info
        if kind == FileKind.VIDEO:
            return extract_video(path), True, info
        if kind == FileKind.CODE:
            if path.endswith(".ipynb"):
                return extract_notebook(path), True, info
            return extract_code(path), True, info

        return [], True, info

    @staticmethod
    def _get_resume_offset(file_record: FileRecord) -> int:
        if file_record.state != FileState.PARTIAL:
            return 0
        if file_record.kind == FileKind.PDF:
            return file_record.info.get("last_page", 0)
        if file_record.kind == FileKind.DOC:
            return file_record.info.get("last_chunk", 0)
        return 0

    def _build_embed_inputs(
        self,
        file_record: FileRecord,
        segments: list[ExtractedSegment],
        info: dict,
    ) -> list[str | dict[str, str]]:
        if file_record.kind == FileKind.CODE:
            repo, title = _code_place(file_record.path, self.config.code_folders)
            if repo:
                info["repo"] = repo
        else:
            title = Path(file_record.path).stem

        embed_inputs: list[str | dict[str, str]] = []
        for s in segments:
            if s.modality == Modality.TEXT and isinstance(s.payload, str) and s.payload:
                embed_inputs.append(format_doc_prompt(title, s.payload[:4000]) if has_words(s.payload) else "")
            elif s.modality != Modality.NONE and s.payload:
                embed_inputs.append(s.payload)
            else:
                embed_inputs.append("")
        return embed_inputs

    def _embed_segments(
        self,
        file_record: FileRecord,
        segments: list[ExtractedSegment],
        info: dict,
    ) -> list[bytes | None]:
        if self.ollama is None or not segments:
            return [None] * len(segments)

        embed_inputs = self._build_embed_inputs(file_record, segments, info)
        embeddings = self.ollama.embed(self.config.model, embed_inputs)
        vectors: list[bytes | None] = []
        for idx in range(len(segments)):
            if idx < len(embeddings) and embeddings[idx]:
                vec_arr = l2_normalize(np.array(embeddings[idx], dtype=np.float32))
                vectors.append(float16_to_bytes(vec_arr))
            else:
                vectors.append(None)
        return vectors

    def process_file(self, file_record: FileRecord, target_db: Database) -> bool:
        """Execute pipelined extraction, Ollama embedding, and database commit for a file."""
        if not Path(file_record.path).exists():
            target_db.delete_file(file_record.id)
            return False

        start_offset = self._get_resume_offset(file_record)

        try:
            segments, is_complete, info = self._extract_segments(file_record, start_offset=start_offset)
            vectors = self._embed_segments(file_record, segments, info)
        except Exception as exc:
            target_db.update_file_state(file_record.id, FileState.FAILED, detail=str(exc))
            return False

        for s, vec in zip(segments, vectors, strict=False):
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
                name="",
                body=body,
                original=orig,
            )

        new_state = FileState.DONE if is_complete else FileState.PARTIAL
        target_db.update_file_state(file_record.id, new_state, info=info)
        return True

    def _handle_idle_transition(self) -> None:
        self._update_status_counts()
        self.db.shrink_memory()
        if self.code_db is not None:
            self.code_db.shrink_memory()
        with self._lock:
            if not self.status.paused:
                self.status.activity = EngineActivity.IDLE
                self.status.current_file = ""
        if not self._stop_event.is_set():
            self._work_event.wait()

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            self._pause_event.wait()
            if self._stop_event.is_set():
                break

            self._work_event.clear()
            file_rec, target_db, tier = self._get_next_file()
            if file_rec is None or target_db is None:
                self._handle_idle_transition()
                continue

            with self._lock:
                self.status.activity = (
                    EngineActivity.FINISHING if tier == IndexQueueTier.LONG_FILES else EngineActivity.INDEXING
                )
                self.status.current_file = Path(file_rec.path).name

            try:
                self.process_file(file_rec, target_db)
            except Exception as exc:
                target_db.update_file_state(file_rec.id, FileState.FAILED, detail=str(exc))
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
        with self._lock:
            is_idle = self.status.activity == EngineActivity.IDLE
        if not is_idle:
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
