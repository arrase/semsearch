# Proposal

## Why

When SemSearch sits waiting in the system tray in an idle state, it currently continues consuming background CPU, thread wakeups, SQLite queries, D-Bus updates, and memory resources, and risks `SIGABRT` crashes if a slow `SearchWorker` (`QThread`) exceeds 500ms during cancellation and is garbage-collected while its native thread is still running. As a resident desktop utility, SemSearch must guarantee zero unnecessary CPU wakeups, database polling, D-Bus traffic, or retained UI/document buffers when idle in the system tray, while ensuring safe asynchronous worker lifecycle management.

## What Changes

- **Zero-Resource Idle Guarantee**: Establish a formal specification requirement across background services and GUI components that when the application is waiting/idle in the system tray, it must not consume CPU cycles, periodic thread wakeups, redundant SQLite queries, D-Bus icon updates, or retained preview/result memory resources unless strictly necessary.
- **SQLite Memory Release (`core-storage`)**: Add `Database.shrink_memory()` executing `PRAGMA shrink_memory;` under the database lock to release non-essential SQLite heap and page cache allocations when indexing transitions to idle or when search views return to the system tray.
- **Event-Driven Indexing Worker & Idle Status Caching (`indexing-service`)**: Replace the 0.2-second polling sleep in `IndexService._worker_loop` with an event-driven `_work_event` wait when no files are pending or partial, trigger `shrink_memory()` on both databases when transitioning to `EngineActivity.IDLE`, and avoid redundant `SELECT COUNT(*)` queries in `get_status()` while the engine is `IDLE`.
- **Event-Driven Filesystem Debounce Loop (`desktop-integration`)**: Replace unconditional `check_interval_sec` polling in `FileSystemWatcher._debounce_loop` with a `_wake_event` wait so the debounce thread blocks with zero CPU wakeups whenever all `DebounceBucket` instances are empty, only polling at `check_interval_sec` while buckets hold pending paths.
- **System Tray State Caching & Preview Resource Release (`desktop-gui`)**: Cache the last rendered tray state (`_last_rendered_state`) in `SystemTrayManager.update_status()` so `QPixmap` vector rendering and D-Bus system tray updates occur only when status actually changes; extend `PreviewPane.clear()` and its child preview widgets (`ImagePreviewWidget`, `PdfPreviewWidget`, `VideoPreviewWidget`, `AudioPreviewWidget`, `CodePreviewWidget`) to explicitly close open `pymupdf.Document` handles and release `QPixmap` buffers and editor text.
- **Safe `SearchWorker` (`QThread`) Lifecycle & View Dismissal Cleanup (`search-panel`, `search-window`)**: Track active `SearchWorker` instances in an `_active_workers` set in both `SearchPanel` and `SearchWindow`, connecting each worker's `finished` signal to cleanup and `deleteLater()` so slow workers (>500ms) are never garbage-collected while still running (`QThread: Destroyed while thread is still running` `SIGABRT` fix); release UI result widgets, preview buffers, and SQLite cache memory (`shrink_memory()`) when `SearchPanel.hide_panel()` or `SearchWindow.closeEvent()` returns the application to the system tray.

## Capabilities

### New Capabilities
<!-- None -->

### Modified Capabilities
- `core-storage`: Add requirement for releasing non-essential SQLite heap and page cache memory via `Database.shrink_memory()` (`PRAGMA shrink_memory;`).
- `indexing-service`: Add requirement for event-driven idle waiting (`_work_event`) in `IndexService._worker_loop` with zero polling wakeups when idle, automatic `shrink_memory()` on transition to `IDLE`, and cached status counts in `get_status()` while `IDLE`.
- `desktop-integration`: Update filesystem event monitoring and debouncing requirement so `FileSystemWatcher._debounce_loop` blocks on `_wake_event` with zero wakeups when all `DebounceBucket` instances are empty.
- `desktop-gui`: Update system tray monitor requirement to cache rendered state (`_last_rendered_state`) and avoid redundant `QPixmap` rendering and D-Bus calls while idle, and require explicit resource release across `PreviewPane` and all multimodal preview widgets on `clear()`.
- `search-panel`: Add requirement for safe `SearchWorker` (`QThread`) lifecycle tracking via `_active_workers` and full UI/preview/SQLite memory cleanup when `hide_panel()` dismisses the panel to the system tray.
- `search-window`: Add requirement for safe `SearchWorker` (`QThread`) lifecycle tracking via `_active_workers` and full UI/preview/SQLite memory cleanup when `closeEvent()` closes the window to the system tray.

## Impact

- `src/semsearch/core/database.py`: `Database.shrink_memory()`
- `src/semsearch/services/indexer.py`: `IndexService._work_event`, `_worker_loop()`, `enqueue_file()`, `crawl_and_sync()`, `stop()`, `get_status()`
- `src/semsearch/services/watcher.py`: `DebounceBucket.has_pending()`, `SemSearchEventHandler`, `FileSystemWatcher._wake_event`, `_debounce_loop()`, `stop()`
- `src/semsearch/gui/tray.py`: `SystemTrayManager._last_rendered_state`, `update_status()`, `retranslate_ui()`
- `src/semsearch/gui/preview.py`: `PreviewPane.clear()`, `ImagePreviewWidget.clear()`, `PdfPreviewWidget.clear()`, `VideoPreviewWidget.clear()`, `AudioPreviewWidget.clear()`, `CodePreviewWidget.clear()`
- `src/semsearch/gui/panel.py`: `SearchPanel._active_workers`, `_stop_workers()`, `_cleanup_worker()`, `hide_panel()`
- `src/semsearch/gui/window.py`: `SearchWindow._active_workers`, `_stop_workers()`, `_cleanup_worker()`, `closeEvent()`
- Unit tests in `tests/test_database.py`, `tests/test_indexer.py`, `tests/test_watcher.py`, `tests/test_tray.py`, `tests/test_preview.py`, `tests/test_panel.py`, and `tests/test_window.py`
