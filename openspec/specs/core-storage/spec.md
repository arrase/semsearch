# core-storage Specification

## Purpose
The core-storage capability provides SQLite WAL database management, schema v6 enforcement with automatic v5-to-v6 migration, nested atomic write transactions, lock-free thread-local read connections, RAM-cached vector snapshots, model fingerprint tracking, batch lookups, cascading deletions, and full-text keyword indexing.

## Requirements

### Requirement: Database Schema v6 and Automatic Migration
The storage engine MUST initialize SQLite databases using WAL journal mode, `synchronous = NORMAL`, `foreign_keys = ON`, `busy_timeout = 5000`, and `SCHEMA_VERSION = 6` (`PRAGMA user_version = 6`). The `segments` table MUST include `focus REAL NOT NULL DEFAULT 0.0` and `has_vec INTEGER NOT NULL DEFAULT 0`. When opening an existing database with `0 < user_version < 6`, `_init_db()` MUST migrate it automatically without data loss by adding missing `focus` and `has_vec` columns (backfilling `has_vec = 1 WHERE vec IS NOT NULL`), creating indexes (`files_inode`, `files_state`, `files_state_mtime ON files(state, mtime DESC)`, `segments_file`, `segments_has_vec`), and creating the `segments_fts_delete` `AFTER DELETE ON segments` trigger to remove orphaned `fts` rows.

#### Scenario: Fresh database initialization
- **WHEN** a `Database` instance is opened for a new index path
- **THEN** WAL mode, foreign keys, `user_version = 6`, tables `meta`, `files`, `segments` (with `focus` and `has_vec`), virtual table `fts`, all indexes, and trigger `segments_fts_delete` are created atomically

#### Scenario: Automatic migration from schema v5 to v6
- **WHEN** a `Database` instance opens an existing v5 SQLite database
- **THEN** `focus` and `has_vec` are added to `segments`, existing rows with non-null `vec` receive `has_vec = 1`, `files_state_mtime` and `segments_has_vec` indexes and trigger `segments_fts_delete` are installed, and `user_version` is updated to `6` without losing indexed records

### Requirement: Atomic Nested Write Transactions and Thread-Local WAL Readers
The storage engine MUST serialize writes over a dedicated `_write_conn` guarded by a reentrant lock (`threading.RLock`) using nested `with db.transaction():` blocks that execute `BEGIN IMMEDIATE` on outermost entry, `COMMIT` on normal completion, and `ROLLBACK` on any `BaseException`. For file-backed databases, read queries MUST execute on per-thread read-only connections (`threading.local()` opened with `?mode=ro`, `PRAGMA busy_timeout = 5000`, and `PRAGMA query_only = ON`) so search and GUI threads never contend with the writer lock. `close()` MUST close all thread-local read connections and the write connection.

#### Scenario: Transactional rollback on exception
- **WHEN** an exception is raised inside `with db.transaction():` after inserting or updating rows
- **THEN** the transaction executes `ROLLBACK`, discards partial mutations, and leaves the vector cache generation unchanged

#### Scenario: Concurrent WAL reads during active writes
- **WHEN** background indexing holds `_write_lock` inside a write transaction while search or GUI threads execute read queries
- **THEN** read threads use independent thread-local read-only WAL connections without blocking on `_write_lock` or raising SQLite concurrency errors

### Requirement: File Lifecycle, Path Moves, and Segment Cleanup
The storage engine MUST support inserting candidate files (creating the initial `SegmentKind.NAME` segment and FTS row with home-relative folder text via `Path.relative_to()`), requeuing modified files (deleting non-name segments and refreshing the `'name'` FTS entry), moving files in place (`move_file()` without mutating segments or vectors), deleting non-name segments (`delete_non_name_segments(file_id)`), and deleting files with cascading segment and FTS removal via `ON DELETE CASCADE` and `segments_fts_delete`.

#### Scenario: Insert and move file preserving segments and vectors
- **WHEN** a candidate file is inserted and subsequently moved to a new path via `move_file()`
- **THEN** the file path and its `'name'` FTS row are updated without altering its segment IDs or embedding vectors

#### Scenario: Delete file or non-name segments cascades FTS cleanup
- **WHEN** `delete_file(file_id)` or `delete_non_name_segments(file_id)` executes
- **THEN** deleted segments automatically trigger `segments_fts_delete` to purge their corresponding `fts` virtual table rows and mark the vector snapshot cache dirty

### Requirement: Embedding Model Fingerprint and Full Reindex Reset
The storage engine MUST maintain `embedding_model` and `embedding_dim` metadata keys in `meta` via `sync_model_metadata(model, dim=0) -> bool` and provide `reset_all_for_reindex() -> int`. When a model name or positive vector dimension differs from the stored fingerprint and indexed records exist, `sync_model_metadata()` MUST invoke `reset_all_for_reindex()` atomically to delete all non-name segments, reset all files to `pending`, invalidate the vector cache, and record the new fingerprint.

#### Scenario: Automatic index invalidation on model or dimension change
- **WHEN** `sync_model_metadata()` detects that the configured model or returned embedding dimension differs from the stored `meta` values on a populated database
- **THEN** all non-name segments and vectors are purged, all files are reset to `pending`, `meta` is updated to the new model/dimension, and `True` is returned

### Requirement: RAM-Cached VectorSnapshot and Batch Metadata Lookups
The storage engine MUST cache an immutable RAM `VectorSnapshot` (`segment_ids`, `file_ids`, `modality_codes`, `matrix`, `scale_mask`) via `get_vector_snapshot()` invalidated by an atomic `_vec_generation` counter on committed vector mutations, dynamically inferring vector dimension (`len(blob) // 2`) and skipping blobs with inconsistent lengths. The storage engine MUST also provide chunked batch lookups `find_by_ids(file_ids)` and `get_segments_by_ids(segment_ids) -> dict[int, SegmentMeta]` to eliminate N+1 query loops during search.

#### Scenario: Reusing cached vector snapshot across repeated searches
- **WHEN** `get_vector_snapshot()` is invoked multiple times with no intervening committed vector writes
- **THEN** the cached read-only `VectorSnapshot` is returned immediately from RAM without querying SQLite

#### Scenario: Invalidating vector snapshot after committed vector mutation
- **WHEN** a segment with a vector is inserted or deleted and its enclosing transaction commits
- **THEN** `_vec_generation` increments and the next `get_vector_snapshot()` rebuilds the snapshot with the updated matrix and dynamic dimension

#### Scenario: Batch fetching files and segments
- **WHEN** `find_by_ids()` or `get_segments_by_ids()` is called with hundreds of candidate IDs
- **THEN** records are fetched in chunked `IN (...)` SQL queries (`<= 900` variables per statement) returning ID-keyed mappings including `SegmentMeta.focus`

### Requirement: Full-Text Search, Deduplicated Original Text, and Idle Memory Release
The storage engine MUST avoid storing duplicate text in `fts.original` when it equals normalized `fts.body`, support BM25-ranked `fts_search()` (`bm25(fts, 10.0, 1.0)`) with escaped FTS expressions and optional deferred snippet extraction (`terms=None`), report indexed summary counts via `stats()` (using `has_vec = 1`), and release unused SQLite memory via `shrink_memory()` (`PRAGMA shrink_memory;`).

#### Scenario: FTS keyword search with boosted filename weight and deferred snippets
- **WHEN** `fts_search(query, limit, terms=None)` executes against indexed segments
- **THEN** matching rows are returned ranked by `bm25(fts, 10.0, 1.0)` without running Python snippet extraction until final result assembly

#### Scenario: Shrinking database memory on transition to idle
- **WHEN** `shrink_memory()` is invoked on an open `Database` after indexing or search completes
- **THEN** the database executes `PRAGMA shrink_memory;` synchronized by `_write_lock` without closing connections or invalidating stored records
