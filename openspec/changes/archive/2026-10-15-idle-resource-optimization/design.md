# Design

## Context

See `proposal.md` for motivation. SemSearch runs as a persistent desktop background process with a system tray icon (`SystemTrayManager`), a background indexing worker (`IndexService`), an inotify filesystem watcher (`FileSystemWatcher`), and two on-demand Qt search views (`SearchPanel` and `SearchWindow`). Currently:

1. `IndexService._worker_loop` sleeps for `0.2s` in a tight polling loop whenever no files are queued, causing 5 thread wakeups and multiple SQLite `SELECT` queries (`list_pending`, `find_partial`) per second forever.
2. `FileSystemWatcher._debounce_loop` wakes every `0.25s` (`check_interval_sec`) to iterate over all `DebounceBucket` instances even when no filesystem events have occurred.
3. `SystemTrayManager._poll_status` fires every `1000ms`, calling `IndexService.get_status()` (which executes 8 `SELECT COUNT(*)` queries across `db` and `code_db`) and unconditionally repainting a 64x64 `QPixmap` and transmitting it over D-Bus via `tray_icon.setIcon()`.
4. `PreviewPane.clear()` only switches the `QStackedWidget` index to `0` (`EmptyPreviewWidget`), leaving open `pymupdf.Document` handles, decoded full-resolution and scaled `QPixmap` buffers, and `CodeEditor` text buffers in memory after views are dismissed, and `SearchPanel.hide_panel()` / `SearchWindow.closeEvent()` do not clear results or release SQLite page caches (`PRAGMA shrink_memory;`).
5. `SearchPanel._stop_workers()` and `SearchWindow._stop_workers()` call `self._worker.wait(500)` and then overwrite `self._worker` when starting the next search. If an Ollama embedding request or database query takes longer than 500ms, Python garbage-collects the `SearchWorker` (`QThread`) wrapper while the underlying C++ thread is still running, triggering `Fatal Python error: Aborted` (`QThread: Destroyed while thread is still running`).

## Goals / Non-Goals

**Goals:**
- Achieve zero background thread wakeups in `IndexService._worker_loop` and `FileSystemWatcher._debounce_loop` when idle.
- Eliminate redundant SQLite `SELECT COUNT(*)` queries and D-Bus `QPixmap` tray icon updates during idle tray operation.
- Explicitly release UI result widgets, preview media buffers (`QPixmap`, `pymupdf.Document`, editor text), and non-essential SQLite memory (`PRAGMA shrink_memory;`) when entering idle or dismissing search views to the system tray.
- Prevent `QThread` premature destruction (`SIGABRT`) when cancelling slow `SearchWorker` threads.

**Non-Goals:**
- Changing the SQLite schema or vector storage format.
- Altering debounce time windows (`0.75s` for general folders, `60s`/`600s` for code folders) when filesystem events are actively arriving.
- Introducing external process managers or multi-process IPC for search execution.

## Decisions

### 1. Event-Driven Idle Waiting in `IndexService._worker_loop`
- **Decision**: Introduce `self._work_event = threading.Event()` in `IndexService`. When `_get_next_file()` returns `(None, None, None)`, clear `_work_event`, re-check `_get_next_file()` once to prevent a lost wakeup race, transition `self.status.activity` to `EngineActivity.IDLE` (calling `self.db.shrink_memory()` and `self.code_db.shrink_memory()` on the transition into `IDLE`), and block on `self._work_event.wait()`. Set `_work_event` in `start()`, `stop()`, `resume()`, `enqueue_file()`, and `crawl_and_sync()`.
- **Rationale**: `threading.Event.wait()` blocks on a kernel futex with zero CPU usage and wakes immediately when work arrives or the service stops. Re-checking `_get_next_file()` after `_work_event.clear()` guarantees that an `enqueue_file()` call racing with the idle check cannot leave a file stranded in the queue.
- **Alternatives Considered**: Increasing the polling interval from `0.2s` to `2.0s`. Rejected because any periodic polling violates the zero-wakeup idle requirement and adds latency to file indexing.

### 2. Avoiding Redundant SQLite `stats()` Queries in `IndexService.get_status()`
- **Decision**: In `IndexService.get_status()`, only call `self._update_status_counts()` when `self.status.activity != EngineActivity.IDLE`. Because `_worker_loop`, `crawl_and_sync()`, and `enqueue_file()` already update status counts or wake the worker upon state changes, the cached counters in `self.status` are already authoritative while `IDLE`.
- **Rationale**: Eliminates 8 `SELECT COUNT(*)` queries per second from the system tray's 1-second timer while the engine is idle.
- **Alternatives Considered**: Stopping the tray's `QTimer` from inside `IndexService` via Qt signals across threads. Rejected to keep `IndexService` decoupled from `PySide6` threading primitives while achieving zero SQLite queries and zero D-Bus traffic when combined with tray state caching.

### 3. Event-Driven Debounce Loop in `FileSystemWatcher`
- **Decision**: Add `DebounceBucket.has_pending() -> bool` (checking `bool(self.paths)` under `self._lock`) and `FileSystemWatcher._wake_event = threading.Event()`. Pass `_wake_event` to `SemSearchEventHandler` so that whenever a path is added to a bucket (`on_created`, `on_modified`, `on_moved`), `_wake_event.set()` is called. In `FileSystemWatcher._debounce_loop`, inspect buckets and check if any bucket still has pending paths (`has_pending()`). If at least one bucket has pending paths, wait `self._stop_event.wait(self.check_interval_sec)`. If all buckets are empty, clear `_wake_event`, re-check `any(b.has_pending() for b in self._buckets.values())` (and `not self._stop_event.is_set()`), and block on `self._wake_event.wait()`. Also set `_wake_event` in `stop()`.
- **Rationale**: Preserves the exact `check_interval_sec` (`0.25s`) debounce resolution while files are actively being modified, and drops to zero wakeups the moment all buckets flush.
- **Alternatives Considered**: Spawning one `threading.Timer` per filesystem event. Rejected because rapid edits in code repositories generate thousands of events and timer allocations, whereas a single thread blocking on `_wake_event` when idle is simpler and allocation-free.

### 4. System Tray State Caching in `SystemTrayManager`
- **Decision**: Track `self._last_rendered_state: tuple | None = None` in `SystemTrayManager`, where the state tuple captures `(status.activity, is_paused, int(percent), status.indexed, status.total)`. In `update_status(status)`, compute the tuple and return immediately if it equals `self._last_rendered_state`. Reset `self._last_rendered_state = None` in `retranslate_ui()` before refreshing labels.
- **Rationale**: Prevents allocating and painting a 64x64 `QPixmap` and making D-Bus calls every second when nothing has changed.

### 5. Explicit Multimodal Preview & View Dismissal Memory Release
- **Decision**:
  - Add `Database.shrink_memory()` executing `PRAGMA shrink_memory;` inside `with self._lock:`.
  - Add `.clear()` methods to `ImagePreviewWidget` (clearing `self._pixmap` and `self.image_label`), `PdfPreviewWidget` (closing `self._doc`, setting `self._doc = None`, clearing `self._rendered_pixmap` and `self.page_label`), `VideoPreviewWidget` (clearing `self._poster_pixmap`, `self.poster_label`, and `self._path`), `AudioPreviewWidget` (resetting `self._path`), and `CodePreviewWidget` (clearing `self.editor` text and extra selections). Call all five from `PreviewPane.clear()`.
  - In `SearchPanel.hide_panel()`, clear `search_input`, `result_list`, `preview_pane`, and `_current_result_set`, collapse the geometry, and call `shrink_memory()` on `search_service.db` and `search_service.code_db`.
  - In `SearchWindow.closeEvent()`, clear `search_input`, call `_clear_results()`, and call `shrink_memory()` on `search_service.db` and `search_service.code_db`.
- **Rationale**: Releases decoded image/PDF/video frame pixmaps, open MuPDF file descriptors/page trees, Qt item widgets, and SQLite page cache memory immediately when the user closes or hides the UI back to the tray.

### 6. Safe `SearchWorker` (`QThread`) Lifecycle via `_active_workers` Set
- **Decision**: In both `SearchPanel` and `SearchWindow`, maintain `self._active_workers: set[SearchWorker] = set()`. When launching a `SearchWorker`, add it to `self._active_workers` and connect `worker.finished.connect(lambda w=worker: self._cleanup_worker(w))`. In `_cleanup_worker(worker)`, discard `worker` from `self._active_workers`, clear `self._worker` if `self._worker is worker`, and call `worker.deleteLater()`. In `_stop_workers()`, disconnect `results_ready` (or rely on `_stopped` / query mismatch), call `worker.stop()` on all active workers, wait briefly (`wait(500)` on `self._worker`), and set `self._worker = None` without dropping the strong reference in `self._active_workers` until `finished` fires.
- **Rationale**: Guarantees that Python never garbage-collects a running `QThread` C++ object even if `OllamaClient.embed()` blocks on network I/O beyond the 500ms wait timeout.

## Risks / Trade-offs

- **[Risk] Lost wakeup race between checking queue/bucket emptiness and calling `Event.wait()`** → **Mitigation**: Always clear the event *before* re-checking the queue/bucket state; only call `wait()` if the re-check confirms emptiness (and set the event on `stop()`).
- **[Risk] Stale `SearchWorker` emitting `results_ready` after a newer query started or view was hidden** → **Mitigation**: `SearchWorker.stop()` sets `_stopped = True` so `run()` skips emitting `results_ready`, and `_on_semantic_results_ready` / `_on_search_results_ready` verify that `query` matches the current non-empty query string before applying results.
