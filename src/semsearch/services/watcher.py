"""Inotify filesystem event monitoring with selective directory watches and differential debouncing."""

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from semsearch.config import AppConfig
from semsearch.core.database import Database
from semsearch.core.rules import (
    CODE_ALLOWED_DOT_DIRS,
    PROJECT_BUNDLE_EXTENSIONS,
    PROJECT_MARKERS,
    SKIPPED_FOLDER_NAMES,
    UNIVERSAL_SKIPPED_FOLDERS,
    classify_file,
    is_inside_code_project,
    is_path_excluded,
    should_skip,
)
from semsearch.core.types import FileRecord
from semsearch.services.indexer import IndexService

_MTIME_TOLERANCE = 0.001


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
    """Filters filesystem events, registers dynamic subdirectory watches, and routes into debouncers."""

    def __init__(
        self,
        root: str,
        *,
        is_code: bool,
        bucket: DebounceBucket,
        db: Database,
        config: AppConfig,
        on_dir_added: Callable[[str], None] | None = None,
        on_dir_removed: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self.root = str(Path(root).resolve())
        self.is_code = is_code
        self.bucket = bucket
        self.db = db
        self.config = config
        self._on_dir_added = on_dir_added
        self._on_dir_removed = on_dir_removed

    def _is_ignored_part(self, part: str, *, is_code_context: bool) -> bool:
        skipped = SKIPPED_FOLDER_NAMES if is_code_context else UNIVERSAL_SKIPPED_FOLDERS
        if part in skipped or part.endswith(".noindex"):
            return True
        if part.startswith(".") and part not in {".", ".."}:
            return not (self.is_code and part in CODE_ALLOWED_DOT_DIRS)
        return False

    def _has_ignored_part(self, p: Path, *, is_code_context: bool) -> bool:
        return any(self._is_ignored_part(part, is_code_context=is_code_context) for part in p.parts)

    def _is_in_code_project(self, path_str: str) -> bool:
        return not self.is_code and self.config.skip_code_projects and is_inside_code_project(path_str, [self.root])

    def _relative_to_root(self, path_obj: Path) -> Path:
        root_path = Path(self.root)
        if path_obj.is_relative_to(root_path):
            return path_obj.relative_to(root_path)
        resolved = path_obj.resolve()
        return resolved.relative_to(root_path) if resolved.is_relative_to(root_path) else resolved

    def _is_ignored_dir(self, path_str: str) -> bool:
        rel = self._relative_to_root(Path(path_str))
        is_code_context = self.is_code or self._is_in_code_project(path_str)
        return self._has_ignored_part(rel, is_code_context=is_code_context) or is_path_excluded(
            path_str, self.config.excluded_folders
        )

    @staticmethod
    def _is_unchanged_file(path_str: str, rec: FileRecord) -> bool:
        try:
            st = Path(path_str).stat()
        except OSError:
            return False
        return st.st_size == rec.size and abs(st.st_mtime - rec.mtime) <= _MTIME_TOLERANCE

    def _should_filter(self, path_str: str) -> bool:
        p = Path(path_str)
        rel = self._relative_to_root(p)
        in_code_project = self._is_in_code_project(path_str)
        is_code_context = self.is_code or in_code_project
        if self._has_ignored_part(rel, is_code_context=is_code_context) or is_path_excluded(
            path_str, self.config.excluded_folders
        ):
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
                in_code_project=in_code_project,
            )
            return skip or classify_file(path_str, is_code_mode=self.is_code) is None

        return False

    def on_created(self, event: FileSystemEvent) -> None:
        src_path = os.fsdecode(event.src_path)
        if event.is_directory:
            if self._on_dir_added is not None and not self._is_ignored_dir(src_path):
                self._on_dir_added(src_path)
            return
        if self._should_filter(src_path):
            return
        self.bucket.add(src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.on_created(event)

    def _handle_dir_moved(self, src_path: str, dest_path: str) -> None:
        src_root = Path(src_path).resolve()
        dest_root = Path(dest_path).resolve()
        if self._on_dir_removed is not None:
            self._on_dir_removed(str(src_root))

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
                if not self._is_unchanged_file(new_p, f):
                    self.bucket.add(new_p)

        if self._on_dir_added is not None and not self._is_ignored_dir(str(dest_root)):
            self._on_dir_added(str(dest_root))

    def on_moved(self, event: FileSystemEvent) -> None:
        src_path = os.fsdecode(event.src_path)
        dest_path = os.fsdecode(getattr(event, "dest_path", ""))
        if event.is_directory:
            self._handle_dir_moved(src_path, dest_path)
            return

        rec = self.db.find_by_path(src_path)
        if rec is not None:
            if self._should_filter(dest_path):
                self.db.delete_file(rec.id)
            else:
                self.db.move_file(rec.id, dest_path)
                if not self._is_unchanged_file(dest_path, rec):
                    self.bucket.add(dest_path)
        elif not self._should_filter(dest_path):
            self.bucket.add(dest_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        src_path = os.fsdecode(event.src_path)
        if event.is_directory:
            if self._on_dir_removed is not None:
                self._on_dir_removed(src_path)
            for f in self.db.find_files_under(src_path):
                self.db.delete_file(f.id)
            return
        rec = self.db.find_by_path(src_path)
        if rec is not None:
            self.db.delete_file(rec.id)


class FileSystemWatcher:
    """Linux inotify filesystem watcher with selective directory watches and differential debouncing."""

    def __init__(
        self,
        index_service: IndexService,
        config: AppConfig,
        check_interval_sec: float = 0.25,
    ) -> None:
        self.index_service = index_service
        self.config = config
        self.check_interval_sec = check_interval_sec

        self._observer: BaseObserver | None = None
        self._buckets: dict[str, DebounceBucket] = {}
        self._watched_dirs: set[str] = set()
        self._watch_lock = threading.Lock()
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
            if Path(p).exists():
                self.index_service.enqueue_file(p)

    def _is_eligible_subdir(self, child_path: Path, *, is_code: bool, is_code_context: bool) -> bool:
        name = child_path.name
        skipped = SKIPPED_FOLDER_NAMES if is_code_context else UNIVERSAL_SKIPPED_FOLDERS
        if name in skipped or name.endswith(".noindex"):
            return False
        if name.startswith(".") and name not in {".", ".."} and not (is_code and name in CODE_ALLOWED_DOT_DIRS):
            return False
        return not is_path_excluded(str(child_path), self.config.excluded_folders)

    def _schedule_single_dir(self, observer: BaseObserver, handler: SemSearchEventHandler, dir_str: str) -> bool:
        with self._watch_lock:
            if dir_str in self._watched_dirs:
                return False
            try:
                observer.schedule(handler, dir_str, recursive=False)
            except OSError:
                return False
            self._watched_dirs.add(dir_str)
            return True

    @staticmethod
    def _scan_watch_dir(current: Path, *, follow_symlinks: bool) -> tuple[list[os.DirEntry[str]], bool]:
        subdirs: list[os.DirEntry[str]] = []
        has_project_marker = False
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if not has_project_marker and (
                        entry.name in PROJECT_MARKERS or Path(entry.name).suffix in PROJECT_BUNDLE_EXTENSIONS
                    ):
                        has_project_marker = True
                    if entry.is_dir(follow_symlinks=follow_symlinks):
                        subdirs.append(entry)
        except OSError:
            return [], False
        return subdirs, has_project_marker

    def _collect_watch_children(
        self,
        subdirs: list[os.DirEntry[str]],
        *,
        is_code: bool,
        is_code_context: bool,
        follow_symlinks: bool,
        visited_dirs: set[tuple[int, int]],
    ) -> list[Path]:
        children: list[Path] = []
        for entry in subdirs:
            child_path = Path(entry.path)
            if not self._is_eligible_subdir(child_path, is_code=is_code, is_code_context=is_code_context):
                continue
            if follow_symlinks:
                try:
                    st = entry.stat(follow_symlinks=True)
                except OSError:
                    continue
                dir_key = (st.st_dev, st.st_ino)
                if dir_key in visited_dirs:
                    continue
                visited_dirs.add(dir_key)
            children.append(child_path)
        return children

    def _schedule_dir_tree(self, root_dir: str, handler: SemSearchEventHandler) -> None:
        observer = self._observer
        if observer is None:
            return
        root_path = Path(root_dir).resolve()
        if not root_path.is_dir() or is_path_excluded(str(root_path), self.config.excluded_folders):
            return

        follow = self.config.follow_symlinks
        try:
            root_stat = root_path.stat()
        except OSError:
            return
        visited_dirs: set[tuple[int, int]] = {(root_stat.st_dev, root_stat.st_ino)}
        initial_in_project = (
            not handler.is_code
            and self.config.skip_code_projects
            and is_inside_code_project(str(root_path), [handler.root])
        )
        stack: list[tuple[Path, bool]] = [(root_path, initial_in_project)]

        while stack:
            current, in_code_project = stack.pop()
            self._schedule_single_dir(observer, handler, str(current))
            subdirs, has_project_marker = self._scan_watch_dir(current, follow_symlinks=follow)
            is_root = str(current) == handler.root
            dir_in_project = (
                not handler.is_code
                and self.config.skip_code_projects
                and (in_code_project or (not is_root and has_project_marker))
            )
            children = self._collect_watch_children(
                subdirs,
                is_code=handler.is_code,
                is_code_context=handler.is_code or dir_in_project,
                follow_symlinks=follow,
                visited_dirs=visited_dirs,
            )
            stack.extend((child_path, dir_in_project) for child_path in children)

    def _untrack_dir(self, dir_path: str) -> None:
        target = Path(dir_path).resolve()
        with self._watch_lock:
            self._watched_dirs = {d for d in self._watched_dirs if not Path(d).is_relative_to(target)}

    def _schedule_single_folder(
        self,
        folder: str,
        *,
        is_code: bool,
        target_db: Database,
        quiet_window: float,
        max_ceiling: float | None = None,
    ) -> None:
        f_path = str(Path(folder).resolve())
        if self._observer is None or not Path(f_path).is_dir():
            return
        bucket = DebounceBucket(
            quiet_window=quiet_window,
            max_ceiling=max_ceiling,
            wake_event=self._wake_event,
        )
        self._buckets[f_path] = bucket

        def on_dir_added(new_dir: str) -> None:
            self._schedule_dir_tree(new_dir, handler)

        handler = SemSearchEventHandler(
            root=f_path,
            is_code=is_code,
            bucket=bucket,
            db=target_db,
            config=self.config,
            on_dir_added=on_dir_added,
            on_dir_removed=self._untrack_dir,
        )
        self._schedule_dir_tree(f_path, handler)

    def _schedule_folders(
        self,
        folders: list[str],
        *,
        is_code: bool,
        target_db: Database,
        quiet_window: float,
        max_ceiling: float | None = None,
    ) -> None:
        for folder in folders:
            self._schedule_single_folder(
                folder,
                is_code=is_code,
                target_db=target_db,
                quiet_window=quiet_window,
                max_ceiling=max_ceiling,
            )

    def start(self) -> None:
        """Start the inotify observer and debouncing worker thread."""
        if self._observer is not None:
            return

        self._observer = Observer()
        self._buckets.clear()
        with self._watch_lock:
            self._watched_dirs.clear()

        self._schedule_folders(
            self.config.folders,
            is_code=False,
            target_db=self.index_service.db,
            quiet_window=0.75,
        )

        if self.config.code_enabled and self.index_service.code_db is not None:
            code_quiet = float(max(1, self.config.code_watch_delay_sec))
            code_ceiling = max(code_quiet * 10.0, 60.0)
            self._schedule_folders(
                self.config.code_folders,
                is_code=True,
                target_db=self.index_service.code_db,
                quiet_window=code_quiet,
                max_ceiling=code_ceiling,
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
        with self._watch_lock:
            self._watched_dirs.clear()
