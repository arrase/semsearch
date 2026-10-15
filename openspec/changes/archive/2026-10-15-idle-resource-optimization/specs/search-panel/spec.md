# search-panel Specification

## ADDED Requirements

### Requirement: Safe Asynchronous SearchWorker Lifecycle Management
The search panel MUST retain active `SearchWorker` (`QThread`) references in an `_active_workers` set until each thread emits `finished`, connecting `finished` to remove the worker reference and call `deleteLater()` so slow workers exceeding 500ms during cancellation are never garbage-collected while still running.

#### Scenario: Slow search worker outliving cancellation timeout
- **WHEN** a new search or panel dismissal stops an active `SearchWorker` whose background execution takes longer than 500ms to finish
- **THEN** the worker remains referenced in `_active_workers` until its `finished` signal fires, preventing premature Python garbage collection and `QThread` `SIGABRT` crashes

### Requirement: Resource and SQLite Memory Cleanup on Panel Dismissal
When `hide_panel()` dismisses the search panel back to the system tray, the panel MUST stop active search workers, clear result list widgets and `_current_result_set`, clear the `PreviewPane` to release preview buffers, collapse its geometry, and invoke `shrink_memory()` on the search databases.

#### Scenario: Dismissing search panel releases UI and database memory
- **WHEN** `hide_panel()` is called to hide the quick search panel
- **THEN** the panel clears `result_list`, resets `_current_result_set`, calls `preview_pane.clear()`, collapses to its compact height, and calls `shrink_memory()` on `db` and `code_db`
