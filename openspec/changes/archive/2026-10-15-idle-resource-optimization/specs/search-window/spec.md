# search-window Specification

## ADDED Requirements

### Requirement: Safe Asynchronous SearchWorker Lifecycle Management in Search Window
The search window MUST retain active `SearchWorker` (`QThread`) references in an `_active_workers` set until each thread emits `finished`, connecting `finished` to remove the worker reference and call `deleteLater()` so slow workers exceeding 500ms during cancellation are never garbage-collected while still running.

#### Scenario: Slow search worker outliving cancellation timeout in window
- **WHEN** a new search or window close stops an active `SearchWorker` whose background execution takes longer than 500ms to finish
- **THEN** the worker remains referenced in `_active_workers` until its `finished` signal fires, preventing premature Python garbage collection and `QThread` `SIGABRT` crashes

### Requirement: Resource and SQLite Memory Cleanup on Window Close
When `closeEvent()` closes the standalone search window back to the system tray, the window MUST clear the search input, clear all result cards, code list items, and preview buffers via `_clear_results()`, and invoke `shrink_memory()` on the search databases.

#### Scenario: Closing search window releases UI cards, previews, and database memory
- **WHEN** the user closes the standalone search window (`closeEvent`)
- **THEN** the window clears `search_input`, invokes `_clear_results()` to destroy result cards and clear `preview_pane`, and calls `shrink_memory()` on `db` and `code_db`
