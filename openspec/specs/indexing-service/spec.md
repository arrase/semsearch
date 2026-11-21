# indexing-service Specification

## Purpose
Provides background multi-tier priority queue indexing, atomic per-file database commits, vector preservation across moves, resilient Ollama outage backoff, independent manual vs policy pausing, model fingerprint synchronization, VRAM idle unloading, selective inotify watching, and power/schedule enforcement.

## Requirements

### Requirement: Multi-Tier Priority Indexing Queues and Unified Filtering
`IndexService` MUST process files in priority order: `NEW_FILES` (up to 50 immediate items), `BACKFILL` (general pending files prioritized by kind and `mtime DESC`), `CODE` (code pending files when `code_enabled`), and `LONG_FILES` (partial PDFs > 30 pages and documents > 40 chunks). `enqueue_file()` MUST apply unified `should_skip()` filtering (honoring secrets, minified assets, `excluded_folders`, `excluded_types` including default `["heic"]` in general mode, and `MAX_TEXT_FILE_BYTES`).

#### Scenario: Unified rule enforcement and default .heic exclusion on enqueue and crawl
- **WHEN** a `.heic` image, secret file, minified JS bundle, or excluded-folder file is encountered during `crawl_and_sync()` or `enqueue_file()` with default settings (`excluded_types = ["heic"]`)
- **THEN** the file is skipped consistently in both paths without creating pending database entries

### Requirement: Vector Preservation Across Moves and Atomic Per-File Commits
`_upsert_candidate()` MUST requeue an existing file record only when `cand.mtime != rec.mtime or cand.size != rec.size`, preserving existing segments and vectors for unchanged `DONE` files after renames or moves. `process_file()` MUST execute extraction and embedding outside the SQLite write lock, skip embedding calls for `Modality.NONE` or empty/non-word payloads (never dispatching `""` to `ollama.embed()`), probe and store `info["duration"]` via `probe_media_duration()` for audio/video files, resolve `{repo.name}/{rel_path}` titles via `_code_place()` for code files, and commit segment deletion (`start_offset == 0`), segment insertion (including `focus=s.focus`), and file state update (`DONE`/`PARTIAL`) inside one `with target_db.transaction():`.

#### Scenario: Preserving vectors when an indexed file is moved or re-enqueued unchanged
- **WHEN** an already `DONE` file is moved (`mv`) without altering its `size` or `mtime`
- **THEN** `_upsert_candidate()` does not call `requeue_file()`, keeping its existing segments and float16 vectors intact

#### Scenario: Zero empty-string embeddings and real media duration metadata
- **WHEN** `process_file()` processes an audio/video file or a file producing a segment with `Modality.NONE` or blank text
- **THEN** `info["duration"]` is populated from `probe_media_duration()`, no `""` input is sent to `ollama.embed()`, and all extracted segments with `focus` and vectors are persisted atomically

### Requirement: Transient Ollama Outage Resilience with Exponential Backoff
When `process_file()` or `_worker_loop()` encounters `(OllamaConnectionError, OllamaTimeoutError)`, the service MUST NOT mark the file as `FAILED`. Instead, it MUST set `status.waiting_for = "Ollama"` and `status.activity = EngineActivity.PAUSED` (unless already paused), re-insert `NEW_FILES` items at the front of `_new_files_queue`, and wait on `_stop_event.wait(backoff)` with exponential backoff (`2.0s` doubling up to `30.0s`, resetting to `2.0s` on success or idle) so shutdown cancels backoff immediately and indexing resumes cleanly once Ollama recovers.

#### Scenario: Recovering automatically from temporary Ollama daemon downtime
- **WHEN** Ollama becomes unreachable or times out while indexing a pending file
- **THEN** the file stays `PENDING` (not `FAILED`), `status.waiting_for` reports `"Ollama"`, the worker applies cancellable exponential backoff up to 30 seconds, and the file indexes automatically once Ollama responds

### Requirement: Independent Manual vs Policy Pause Tracking and Work Listeners
`pause(reason="", *, by_policy=False)` and `resume(*, by_policy=False)` MUST track `_user_paused` and `_policy_paused` independently so pausing via the system tray never conflicts with `PowerScheduler` battery or schedule policies, resuming indexing only when both pause flags are cleared. `add_work_listener(callback)` MUST notify registered listeners whenever indexing work arrives or activity transitions out of `IDLE`.

#### Scenario: Policy resume does not override manual user pause
- **WHEN** the user manually pauses indexing from the tray (`by_policy=False`), `PowerScheduler` later pauses (`by_policy=True`) and subsequently resumes (`by_policy=True`) upon AC reconnection
- **THEN** indexing remains paused until the user explicitly calls `resume(by_policy=False)`

### Requirement: Model Fingerprint Sync, Full Reindex Reset, and Idle VRAM Unloading
`sync_model_state()` MUST synchronize `db.sync_model_metadata(config.model)` across both `db` and `code_db` on startup and crawl (and record `embedding_dim` on first embedding batch). `reset_all_for_reindex()` MUST reset both databases to `pending`, clear `_new_files_queue`, update counters, and wake the worker. Upon transitioning to `IDLE`, `_handle_idle_transition()` MUST call `ollama.unload(config.model)` (`keep_alive: 0`) if embeddings ran since the previous idle state, call `shrink_memory()` on active databases, and block on `_work_event.wait()` with zero CPU polling while `get_status()` returns cached counters without querying SQLite.

#### Scenario: Unloading model and entering zero-wakeup idle
- **WHEN** the indexing worker completes all pending and partial work after embedding one or more files
- **THEN** it calls `ollama.unload(config.model)`, calls `shrink_memory()` on active databases, sets `EngineActivity.IDLE`, blocks on `_work_event`, and serves cached status counters from `get_status()`

### Requirement: Selective Non-Recursive Inotify Watches and Device-Filtered Power Policies
`FileSystemWatcher` MUST install non-recursive (`recursive=False`) watches strictly on eligible directories (skipping dot-directories, `SKIPPED_FOLDER_NAMES`, `.noindex`, and `excluded_folders`), dynamically schedule newly created eligible subdirectories, and handle `on_moved` via `db.move_file()` without re-enqueuing files whose size and mtime remain unchanged. `PowerScheduler` MUST ignore `/sys/class/power_supply/*` entries with `scope == "Device"` (such as wireless peripherals), pause/resume using `by_policy=True`, and block indefinitely on `_wake_event.wait()` without polling whenever both `index_on_battery=True` and `index_schedule_enabled=False`.

#### Scenario: Avoiding inotify watch exhaustion and unchanged move churn
- **WHEN** `FileSystemWatcher` watches a repository containing `.git` and `node_modules`, or an indexed file is moved without modification
- **THEN** ignored subdirectories receive zero kernel inotify watches and unchanged moved files update their DB path without triggering re-embedding

#### Scenario: Ignoring peripheral batteries and suspending polling when policies are inactive
- **WHEN** `/sys/class/power_supply/` contains a discharging mouse battery with `scope == "Device"`, or both battery and schedule policies are disabled
- **THEN** `is_on_battery()` returns `False`, and when both policies are disabled `_run_loop()` blocks on `_wake_event` with zero periodic wakeups until `wake()` or `stop()` is called
