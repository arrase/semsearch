"""Background indexing service, multi-tier priority scheduling, and pipelined execution."""

import contextlib
import threading
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

import numpy as np

from semsearch.config import AppConfig
from semsearch.core.crawler import crawl_folder, purge_outside_roots, sync_candidates
from semsearch.core.database import Database
from semsearch.core.ollama import (
    OllamaClient,
    OllamaConnectionError,
    OllamaError,
    OllamaTimeoutError,
    format_doc_prompt,
)
from semsearch.core.rules import (
    MAX_TEXT_FILE_BYTES,
    classify_file,
    is_code_project_root,
    is_inside_code_project,
    should_skip,
)
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
from semsearch.extractors.media import extract_audio, extract_video, probe_media_duration
from semsearch.extractors.pdfs import extract_pdf


class IndexQueueTier(StrEnum):
    """Priority queue tiers for file indexing."""

    NEW_FILES = "new_files"
    BACKFILL = "backfill"
    CODE = "code"
    LONG_FILES = "long_files"


_MIN_TITLE_PARTS = 2
_MAX_NEW_FILES_QUEUE = 50
_DOC_CHUNK_BATCH_SIZE = 40
_INITIAL_OLLAMA_BACKOFF_SEC = 2.0
_MAX_OLLAMA_BACKOFF_SEC = 30.0
_MAX_CONSECUTIVE_TIMEOUTS = 3


def _code_place(path: str, code_folders: list[str] | None = None) -> tuple[str | None, str]:
    """Determine repo and repo-prefixed title path for code files matching project root markers first."""
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
            rel_path = path_obj.relative_to(repo).as_posix()
        except ValueError:
            return str(repo), path_obj.name
        return str(repo), f"{repo.name}/{rel_path}"

    parts = path_obj.parts
    title = "/".join(parts[-_MIN_TITLE_PARTS:]) if len(parts) >= _MIN_TITLE_PARTS else path_obj.name
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

        self._user_paused = False
        self._policy_paused = False
        self._user_pause_reason = ""
        self._policy_pause_reason = ""
        self._did_embed_since_idle = False
        self._timeout_counts: dict[tuple[int, bool], int] = {}
        self._work_listeners: list[Callable[[], None]] = []

    def add_work_listener(self, callback: Callable[[], None]) -> None:
        """Register callback notified when new work arrives or activity leaves IDLE."""
        with self._lock:
            if callback not in self._work_listeners:
                self._work_listeners.append(callback)

    def _notify_work_listeners(self) -> None:
        with self._lock:
            listeners = list(self._work_listeners)
        for callback in listeners:
            callback()

    def _wake_worker(self) -> None:
        self._work_event.set()
        self._notify_work_listeners()

    def sync_model_state(self) -> None:
        """Synchronize stored embedding model fingerprints across general and code databases."""
        reset = self.db.sync_model_metadata(self.config.model)
        if self.code_db is not None:
            reset = self.code_db.sync_model_metadata(self.config.model) or reset
        if reset:
            self._update_status_counts()
            self._wake_worker()

    def reset_all_for_reindex(self) -> int:
        """Reset both general and code indexes to pending and wake the worker."""
        reset_count = self.db.reset_all_for_reindex()
        if self.code_db is not None:
            reset_count += self.code_db.reset_all_for_reindex()
        with self._lock:
            self._new_files_queue.clear()
            self._timeout_counts.clear()
        self._update_status_counts()
        self._wake_worker()
        return reset_count

    def start(self) -> None:
        """Launch background worker thread."""
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self.sync_model_state()
        self._stop_event.clear()
        with self._lock:
            is_paused = self._user_paused or self._policy_paused
            if not is_paused:
                self.status.activity = EngineActivity.STARTING
        if not is_paused:
            self._pause_event.set()
        self._work_event.set()
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

    def pause(self, reason: str = "", *, by_policy: bool = False) -> None:
        """Pause indexing operations, tracking manual and policy pauses independently."""
        self._pause_event.clear()
        with self._lock:
            was_idle = self.status.activity == EngineActivity.IDLE
            if by_policy:
                self._policy_paused = True
                self._policy_pause_reason = reason
            else:
                self._user_paused = True
                self._user_pause_reason = reason
            self.status.paused = True
            self.status.waiting_for = (
                (self._policy_pause_reason or self._user_pause_reason)
                if by_policy
                else (self._user_pause_reason or self._policy_pause_reason)
            )
            self.status.activity = EngineActivity.PAUSED
        if was_idle:
            self._notify_work_listeners()

    def resume(self, *, by_policy: bool = False) -> None:
        """Resume indexing operations once both manual and policy pauses are cleared."""
        should_resume = False
        with self._lock:
            if by_policy:
                self._policy_paused = False
                self._policy_pause_reason = ""
            else:
                self._user_paused = False
                self._user_pause_reason = ""

            if self._user_paused or self._policy_paused:
                self.status.paused = True
                self.status.waiting_for = self._user_pause_reason or self._policy_pause_reason
                self.status.activity = EngineActivity.PAUSED
            else:
                self.status.paused = False
                self.status.waiting_for = ""
                self.status.activity = EngineActivity.INDEXING
                should_resume = True

        if should_resume:
            self._pause_event.set()
            self._wake_worker()

    def _is_code_file_path(self, resolved_path: Path) -> bool:
        if not (self.config.code_enabled and self.code_db is not None):
            return False
        return any(resolved_path.is_relative_to(Path(cf).resolve()) for cf in self.config.code_folders)

    @staticmethod
    def _upsert_candidate(target_db: Database, rec: FileRecord | None, cand: Candidate) -> int:
        if rec is None:
            return target_db.insert_file(cand)
        if cand.mtime != rec.mtime or cand.size != rec.size:
            target_db.requeue_file(rec.id, cand)
        return rec.id

    def _build_enqueue_candidate(self, resolved_path: Path, resolved: str, *, is_code: bool) -> Candidate | None:
        in_code_project = (
            not is_code and self.config.skip_code_projects and is_inside_code_project(resolved, self.config.folders)
        )
        skip, _ = should_skip(
            resolved,
            is_code_mode=is_code,
            excluded_folders=self.config.excluded_folders,
            excluded_types=None if is_code else self.config.excluded_types,
            in_code_project=in_code_project,
        )
        if skip:
            return None

        try:
            st = resolved_path.stat()
        except OSError:
            return None

        kind = classify_file(resolved, is_code_mode=is_code)
        if kind is None or (kind in (FileKind.DOC, FileKind.CODE) and st.st_size > MAX_TEXT_FILE_BYTES):
            return None

        return Candidate(
            path=resolved,
            kind=kind,
            size=st.st_size,
            mtime=st.st_mtime,
            inode=st.st_ino,
            device=st.st_dev,
        )

    def enqueue_file(self, path: str, tier: IndexQueueTier = IndexQueueTier.NEW_FILES) -> None:
        """Enqueue newly modified or created file for immediate or backfill indexing."""
        resolved_path = Path(path).resolve()
        resolved = str(resolved_path)
        if not resolved_path.is_file():
            return

        is_code = self._is_code_file_path(resolved_path)
        cand = self._build_enqueue_candidate(resolved_path, resolved, is_code=is_code)
        if cand is None:
            return

        target_db = self.code_db if is_code and self.code_db is not None else self.db
        rec = target_db.find_by_path(resolved)
        fid = self._upsert_candidate(target_db, rec, cand)
        needs_work = rec is None or rec.state == FileState.PENDING or cand.mtime != rec.mtime or cand.size != rec.size
        if not needs_work:
            return

        with self._lock:
            if tier == IndexQueueTier.NEW_FILES and len(self._new_files_queue) < _MAX_NEW_FILES_QUEUE:
                item = (fid, is_code)
                if item not in self._new_files_queue:
                    self._new_files_queue.append(item)

        self._update_status_counts()
        self._wake_worker()

    def _crawl_single_root(self, root: str, *, is_code: bool, excl_types: list[str] | None) -> list[Candidate]:
        return crawl_folder(
            root,
            is_code=is_code,
            excluded_folders=self.config.excluded_folders,
            excluded_types=excl_types,
            follow_symlinks=self.config.follow_symlinks,
            skip_code_projects=self.config.skip_code_projects,
            skip_datasets=self.config.skip_datasets,
        )

    def _resolve_sync_target(
        self,
        roots: list[str] | None,
        *,
        is_code: bool,
    ) -> tuple[Database, list[str], list[str] | None] | None:
        if is_code:
            if self.code_db is None:
                return None
            target_db = self.code_db
            default_roots = self.config.code_folders
            excl_types: list[str] | None = None
        else:
            target_db = self.db
            default_roots = self.config.folders
            excl_types = self.config.excluded_types
        scan_roots = default_roots if roots is None else roots
        return target_db, scan_roots, excl_types

    def _enter_sync_state(self) -> None:
        was_idle = False
        with self._lock:
            if not self.status.paused and self.status.activity in (EngineActivity.STARTING, EngineActivity.IDLE):
                was_idle = self.status.activity == EngineActivity.IDLE
                self.status.activity = EngineActivity.SYNCING
        if was_idle:
            self._notify_work_listeners()

    def _finish_sync_state(self) -> None:
        self._update_status_counts()
        with self._lock:
            if self.status.paused:
                self.status.activity = EngineActivity.PAUSED
            elif self.status.activity == EngineActivity.SYNCING:
                self.status.activity = EngineActivity.IDLE
            has_pending = self.status.pending > 0
        self._work_event.set()
        if has_pending:
            self._notify_work_listeners()

    def crawl_and_sync(self, roots: list[str] | None = None, *, is_code: bool = False) -> None:
        """Crawl folders and synchronize database records."""
        if self._stop_event.is_set():
            return
        resolved = self._resolve_sync_target(roots, is_code=is_code)
        if resolved is None:
            return
        target_db, scan_roots, excl_types = resolved

        self.sync_model_state()
        self._enter_sync_state()

        for root in scan_roots:
            if self._stop_event.is_set():
                break
            candidates = self._crawl_single_root(root, is_code=is_code, excl_types=excl_types)
            sync_candidates(target_db, candidates, roots=[root])

        if roots is None and not self._stop_event.is_set():
            purge_outside_roots(target_db, scan_roots)

        self._finish_sync_state()

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
    ) -> tuple[list[ExtractedSegment], bool, dict[str, object]]:
        path = file_record.path
        kind = file_record.kind
        info: dict[str, object] = dict(file_record.info)

        if kind in (FileKind.IMAGE, FileKind.SCREENSHOT):
            return [extract_image(path, is_screenshot=(kind == FileKind.SCREENSHOT))], True, info
        if kind == FileKind.PDF:
            segments, complete = extract_pdf(path, start_page=start_offset, max_pages=30)
            info["last_page"] = start_offset + len(segments)
            return segments, complete, info
        if kind == FileKind.DOC:
            all_segments = extract_doc(path)
            if len(all_segments) > _DOC_CHUNK_BATCH_SIZE:
                end_chunk = min(start_offset + _DOC_CHUNK_BATCH_SIZE, len(all_segments))
                batch = all_segments[start_offset:end_chunk]
                info["last_chunk"] = end_chunk
                return batch, end_chunk >= len(all_segments), info
            return all_segments, True, info
        if kind in (FileKind.AUDIO, FileKind.VIDEO):
            duration = probe_media_duration(path)
            if duration > 0.0:
                info["duration"] = duration
            max_dur = max(0, self.config.max_media_duration_min) * 60
            segments = (
                extract_audio(path, max_duration_sec=max_dur)
                if kind == FileKind.AUDIO
                else extract_video(path, max_duration_sec=max_dur)
            )
            return segments, True, info
        segments = extract_notebook(path) if path.endswith(".ipynb") else extract_code(path)
        return segments, True, info

    @staticmethod
    def _get_resume_offset(file_record: FileRecord) -> int:
        if file_record.state != FileState.PARTIAL:
            return 0
        if file_record.kind == FileKind.PDF:
            val = file_record.info.get("last_page", 0)
        elif file_record.kind == FileKind.DOC:
            val = file_record.info.get("last_chunk", 0)
        else:
            return 0
        return int(val) if isinstance(val, (int, float)) else 0

    def _build_embed_inputs(
        self,
        file_record: FileRecord,
        segments: list[ExtractedSegment],
        info: dict[str, object],
    ) -> list[str | dict[str, str] | None]:
        if file_record.kind == FileKind.CODE:
            repo, title = _code_place(file_record.path, self.config.code_folders)
            if repo:
                info["repo"] = repo
        else:
            title = Path(file_record.path).stem

        embed_inputs: list[str | dict[str, str] | None] = []
        for s in segments:
            if s.modality == Modality.TEXT and isinstance(s.payload, str) and s.payload and has_words(s.payload):
                snippet = s.payload[:4000]
                formatted = (
                    self.ollama.format_doc_for_model(self.config.model, title, snippet)
                    if self.ollama is not None
                    else format_doc_prompt(title, snippet)
                )
                embed_inputs.append(formatted)
            elif s.modality not in (Modality.NONE, Modality.TEXT) and s.payload:
                embed_inputs.append(s.payload)
            else:
                embed_inputs.append(None)
        return embed_inputs

    def _encode_embeddings(
        self,
        target_db: Database,
        segment_count: int,
        valid_indices: list[int],
        embeddings: list[list[float]],
    ) -> list[bytes | None]:
        vectors: list[bytes | None] = [None] * segment_count
        first_dim: int | None = None
        for idx, emb in zip(valid_indices, embeddings, strict=False):
            if not emb:
                continue
            if first_dim is None:
                first_dim = len(emb)
            vec_arr = l2_normalize(np.array(emb, dtype=np.float32))
            vectors[idx] = float16_to_bytes(vec_arr)
        if first_dim is not None and target_db.get_meta("embedding_dim") is None:
            target_db.sync_model_metadata(self.config.model, dim=first_dim)
        return vectors

    def _embed_segments(
        self,
        target_db: Database,
        file_record: FileRecord,
        segments: list[ExtractedSegment],
        info: dict[str, object],
    ) -> list[bytes | None]:
        if self.ollama is None or not segments:
            return [None] * len(segments)

        raw_inputs = self._build_embed_inputs(file_record, segments, info)
        valid_indices: list[int] = []
        valid_inputs: list[str | dict[str, str]] = []
        for idx, item in enumerate(raw_inputs):
            if item:
                valid_indices.append(idx)
                valid_inputs.append(item)

        if not valid_inputs:
            return [None] * len(segments)

        embeddings = self.ollama.embed(self.config.model, valid_inputs)
        self._did_embed_since_idle = True
        return self._encode_embeddings(target_db, len(segments), valid_indices, embeddings)

    def process_file(self, file_record: FileRecord, target_db: Database) -> bool:
        """Execute pipelined extraction, Ollama embedding, and atomic database commit for a file."""
        if not Path(file_record.path).exists():
            target_db.delete_file(file_record.id)
            return False

        start_offset = self._get_resume_offset(file_record)

        try:
            segments, is_complete, info = self._extract_segments(file_record, start_offset=start_offset)
            vectors = self._embed_segments(target_db, file_record, segments, info)
        except (OllamaConnectionError, OllamaTimeoutError):
            with self._lock:
                if not self.status.paused:
                    self.status.waiting_for = "Ollama"
                    self.status.activity = EngineActivity.PAUSED
            raise
        except Exception as exc:  # noqa: BLE001 — boundary handler so corrupt files or model errors mark file FAILED without crashing indexer
            target_db.update_file_state(file_record.id, FileState.FAILED, detail=str(exc))
            return False

        with target_db.transaction():
            if start_offset == 0:
                target_db.delete_non_name_segments(file_record.id)
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
                    focus=s.focus,
                )

            new_state = FileState.DONE if is_complete else FileState.PARTIAL
            target_db.update_file_state(file_record.id, new_state, info=info)

        with self._lock:
            if self.status.waiting_for == "Ollama":
                self.status.waiting_for = ""
        return True

    def _handle_idle_transition(self) -> None:
        self._update_status_counts()
        if self._did_embed_since_idle:
            self._did_embed_since_idle = False
            if self.ollama is not None and self.config.ollama_keep_alive == "unload_idle":
                with contextlib.suppress(OllamaError):
                    self.ollama.unload(self.config.model)
        self.db.shrink_memory()
        if self.code_db is not None:
            self.code_db.shrink_memory()
        with self._lock:
            if not self.status.paused:
                self.status.activity = EngineActivity.IDLE
                self.status.current_file = ""
        if not self._stop_event.is_set():
            self._work_event.wait()

    def _requeue_front_new_file(self, file_rec: FileRecord, target_db: Database) -> None:
        item = (file_rec.id, target_db is self.code_db)
        with self._lock:
            if item not in self._new_files_queue and len(self._new_files_queue) < _MAX_NEW_FILES_QUEUE:
                self._new_files_queue.insert(0, item)

    def _backoff_for_ollama(
        self,
        file_rec: FileRecord,
        target_db: Database,
        tier: IndexQueueTier | None,
        backoff: float,
    ) -> float:
        if tier == IndexQueueTier.NEW_FILES:
            self._requeue_front_new_file(file_rec, target_db)
        with self._lock:
            if not self.status.paused:
                self.status.waiting_for = "Ollama"
                self.status.activity = EngineActivity.PAUSED
        self._stop_event.wait(backoff)
        return min(backoff * 2.0, _MAX_OLLAMA_BACKOFF_SEC)

    def _run_worker_step(
        self,
        file_rec: FileRecord,
        target_db: Database,
        tier: IndexQueueTier | None,
        backoff: float,
    ) -> float:
        with self._lock:
            was_idle = self.status.activity == EngineActivity.IDLE
            self.status.activity = (
                EngineActivity.FINISHING if tier == IndexQueueTier.LONG_FILES else EngineActivity.INDEXING
            )
            self.status.current_file = Path(file_rec.path).name
        if was_idle:
            self._notify_work_listeners()

        key = (file_rec.id, target_db is self.code_db)
        try:
            self.process_file(file_rec, target_db)
        except OllamaTimeoutError as exc:
            count = self._timeout_counts.get(key, 0) + 1
            if count >= _MAX_CONSECUTIVE_TIMEOUTS:
                self._timeout_counts.pop(key, None)
                target_db.update_file_state(file_rec.id, FileState.FAILED, detail=str(exc))
                with self._lock:
                    if self.status.waiting_for == "Ollama":
                        self.status.waiting_for = ""
                return _INITIAL_OLLAMA_BACKOFF_SEC
            self._timeout_counts[key] = count
            return self._backoff_for_ollama(file_rec, target_db, tier, backoff)
        except OllamaConnectionError:
            return self._backoff_for_ollama(file_rec, target_db, tier, backoff)
        except Exception as exc:  # noqa: BLE001 — worker loop safety net so unexpected errors do not kill background thread
            self._timeout_counts.pop(key, None)
            target_db.update_file_state(file_rec.id, FileState.FAILED, detail=str(exc))
            return backoff
        else:
            self._timeout_counts.pop(key, None)
            return _INITIAL_OLLAMA_BACKOFF_SEC

    def _worker_loop(self) -> None:
        backoff = _INITIAL_OLLAMA_BACKOFF_SEC
        while not self._stop_event.is_set():
            self._pause_event.wait()
            if self._stop_event.is_set():
                break

            self._work_event.clear()
            file_rec, target_db, tier = self._get_next_file()
            if file_rec is None or target_db is None:
                backoff = _INITIAL_OLLAMA_BACKOFF_SEC
                self._handle_idle_transition()
                continue

            backoff = self._run_worker_step(file_rec, target_db, tier, backoff)
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
