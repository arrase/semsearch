"""Inotify filesystem event monitoring and differential debouncing."""

import os
from pathlib import Path
import threading
import time
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from semsearch.config import AppConfig
from semsearch.core.database import Database
from semsearch.core.rules import (
    SKIPPED_FOLDER_NAMES,
    classify_file,
    is_path_excluded,
    should_skip,
)
from semsearch.services.indexer import IndexService


class DebounceBucket:
    """Tracks filesystem event timestamps for quiet-window and ceiling debouncing."""

    def __init__(
        self,
        quiet_window: float,
        max_ceiling: float | None = None,
        wake_event: threading.Event | None = None,
    ) -> None:
        self.quiet_window = quiet_window
        self.max_ceiling = max_ceiling
        self.wake_event = wake_event
        self.first_time: float | None = None
        self.last_time: float | None = None
        self.paths: set[str] = set()
        self._lock = threading.Lock()

    def add(self, path: str, now: float | None = None) -> None:
        """Register a path event."""
        with self._lock:
            current = now if now is not None else time.time()
            if self.first_time is None:
                self.first_time = current
            self.last_time = current
            self.paths.add(path)
        if self.wake_event is not None:
            self.wake_event.set()

    def has_pending(self) -> bool:
        """Return True if the bucket currently holds pending paths."""
        with self._lock:
            return bool(self.paths)

    def is_ready(self, now: float | None = None) -> bool:
        """Check if debounce timer expired via quiet period or ceiling."""
        with self._lock:
            if self.first_time is None or self.last_time is None or not self.paths:
                return False
            current = now if now is not None else time.time()
            if self.max_ceiling is not None and (current - self.first_time) >= self.max_ceiling:
                return True
            return (current - self.last_time) >= self.quiet_window

    def flush(self) -> set[str]:
        """Clear state and return accumulated paths."""
        with self._lock:
            accum = set(self.paths)
            self.paths.clear()
            self.first_time = None
            self.last_time = None
            return accum


class SemSearchEventHandler(FileSystemEventHandler):
    """Filters filesystem events and routes them into debouncers."""

    def __init__(
        self,
        root: str,
        is_code: bool,
        bucket: DebounceBucket,
        db: Database,
        config: AppConfig,
    ) -> None:
        super().__init__()
        self.root = root
        self.is_code = is_code
        self.bucket = bucket
        self.db = db
        self.config = config

    def _should_filter(self, path_str: str) -> bool:
        p = Path(path_str)
        for part in p.parts:
            if part.startswith(".") and part != "." and part != "..":
                return True
            if part in SKIPPED_FOLDER_NAMES or part.endswith(".noindex"):
                return True

        if is_path_excluded(path_str, self.config.excluded_folders):
            return True

        ext = p.suffix.lstrip(".").lower()
        norm_excl = {t.lstrip(".").strip().lower() for t in self.config.excluded_types if t.strip()}
        if not self.is_code and ext in norm_excl:
            return True

        if p.is_file():
            skip, _ = should_skip(
                path_str,
                is_code_mode=self.is_code,
                excluded_folders=self.config.excluded_folders,
                excluded_types=None if self.is_code else self.config.excluded_types,
            )
            if skip:
                return True
            if classify_file(path_str, is_code_mode=self.is_code) is None:
                return True

        return False


    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory or self._should_filter(event.src_path):
            return
        self.bucket.add(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory or self._should_filter(event.src_path):
            return
        self.bucket.add(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        dest_path = getattr(event, "dest_path", "")
        if event.is_directory:
            src_root = Path(event.src_path).resolve()
            dest_root = Path(dest_path).resolve()
            for f in self.db.find_files_under(str(src_root)):
                try:
                    rel = Path(f.path).resolve().relative_to(src_root)
                    new_p = str(dest_root / rel)
                except ValueError:
                    continue

                if self._should_filter(new_p):
                    self.db.delete_file(f.id)
                else:
                    self.db.move_file(f.id, new_p)
                    self.bucket.add(new_p)
            return

        rec = self.db.find_by_path(event.src_path)
        if rec is not None:
            if self._should_filter(dest_path):
                self.db.delete_file(rec.id)
            else:
                self.db.move_file(rec.id, dest_path)
                self.bucket.add(dest_path)
        elif not self._should_filter(dest_path):
            self.bucket.add(dest_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            for f in self.db.find_files_under(event.src_path):
                self.db.delete_file(f.id)
            return
        rec = self.db.find_by_path(event.src_path)
        if rec is not None:
            self.db.delete_file(rec.id)


class FileSystemWatcher:
    """Linux inotify filesystem watcher with differential debouncing for general and code folders."""

    def __init__(
        self,
        index_service: IndexService,
        config: AppConfig,
        check_interval_sec: float = 0.25,
    ) -> None:
        self.index_service = index_service
        self.config = config
        self.check_interval_sec = check_interval_sec

        self._observer: Observer | None = None
        self._buckets: dict[str, DebounceBucket] = {}
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._timer_thread: threading.Thread | None = None

    def _debounce_loop(self) -> None:
        while not self._stop_event.is_set():
            self._wake_event.clear()
            now = time.time()
            buckets = list(self._buckets.values())
            for bucket in buckets:
                if bucket.is_ready(now):
                    flushed = bucket.flush()
                    if flushed:
                        self._sync_paths(flushed)
            if any(b.has_pending() for b in buckets):
                self._stop_event.wait(self.check_interval_sec)
            elif not self._stop_event.is_set():
                self._wake_event.wait()

    def _sync_paths(self, paths: set[str]) -> None:
        for p in paths:
            if os.path.exists(p):
                self.index_service.enqueue_file(p)

    def _schedule_folders(
        self,
        folders: list[str],
        is_code: bool,
        target_db: Database,
        quiet_window: float,
        max_ceiling: float | None = None,
    ) -> None:
        assert self._observer is not None
        for folder in folders:
            f_path = str(Path(folder).resolve())
            if os.path.isdir(f_path):
                bucket = DebounceBucket(
                    quiet_window=quiet_window,
                    max_ceiling=max_ceiling,
                    wake_event=self._wake_event,
                )
                self._buckets[f_path] = bucket
                handler = SemSearchEventHandler(
                    root=f_path,
                    is_code=is_code,
                    bucket=bucket,
                    db=target_db,
                    config=self.config,
                )
                self._observer.schedule(handler, f_path, recursive=True)

    def start(self) -> None:
        """Start the inotify observer and debouncing worker thread."""
        if self._observer is not None:
            return

        self._observer = Observer()
        self._buckets.clear()

        self._schedule_folders(
            self.config.folders,
            is_code=False,
            target_db=self.index_service.db,
            quiet_window=0.75,
        )

        if self.config.code_enabled and self.index_service.code_db is not None:
            self._schedule_folders(
                self.config.code_folders,
                is_code=True,
                target_db=self.index_service.code_db,
                quiet_window=60.0,
                max_ceiling=600.0,
            )

        self._stop_event.clear()
        self._timer_thread = threading.Thread(target=self._debounce_loop, name="WatcherDebounceThread", daemon=True)
        self._timer_thread.start()
        self._observer.start()

    def stop(self) -> None:
        """Stop observer and debouncing thread."""
        self._stop_event.set()
        self._wake_event.set()
        if self._timer_thread is not None and self._timer_thread.is_alive():
            self._timer_thread.join(timeout=1.0)
        self._timer_thread = None

        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=1.0)
            self._observer = None
