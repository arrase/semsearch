# indexing-service Specification

## Purpose
Provides background multi-tier priority queue indexing, pipelined two-stage processing, partial file continuation, dynamic cost estimation, and status monitoring.

## Requirements

### Requirement: Multi-Tier Priority Indexing Queues
The indexing service MUST process pending files across distinct priority tiers: an immediate fast-track tier for newly added files (up to 50 files), a standard backfill tier for existing indexed folders, a code indexing tier, and a deferred long files completion tier.

#### Scenario: Immediate indexing of newly discovered files
- **WHEN** up to 50 newly created files are detected in watched directories
- **THEN** they are scheduled in the immediate fast-track tier ahead of existing background backfill tasks

#### Scenario: Sequential tier execution during full backfill
- **WHEN** an initial system indexing run commences
- **THEN** files are processed in order: immediate new files, general document backfill, source code repositories, and deferred long file segments

### Requirement: Two-Stage Pipelined Indexing Execution
The indexing service MUST decouple file reading and model inference from database persistence by preparing segments and embedding batches on a background worker while committing transactions atomically on the database worker.

#### Scenario: Asynchronous prepare and atomic database commit
- **WHEN** a batch of files is queued for indexing
- **THEN** content extraction and embedding generation run concurrently without locking the SQLite database until segment batch persistence

#### Scenario: Search availability during active indexing
- **WHEN** background indexing is actively embedding segments
- **THEN** incoming search queries continue reading from SQLite WAL storage without blocking

### Requirement: Partial File Processing and Continuation
The indexing service MUST index initial segments of large multi-part files up to a configured threshold in the primary pass, mark the file as partial, and defer subsequent segments to the background completion queue.

#### Scenario: Initial indexing of large multi-page document
- **WHEN** a document exceeding the initial page limit is first processed
- **THEN** its initial pages are embedded, the file state is marked partial with resumption metadata, and initial pages become searchable immediately

#### Scenario: Resuming and completing partial document segments
- **WHEN** the background long files completion queue executes
- **THEN** indexing resumes from the last indexed page offset and marks the file as fully indexed upon completion

### Requirement: Dynamic Cost Estimation and Engine Status Tracking
The indexing service MUST maintain running throughput measurements for segment embedding and database growth, publishing updated remaining duration estimates, storage projections, and engine lifecycle states. While the engine is in the `IDLE` state, `get_status()` MUST return cached status counters without executing redundant `SELECT COUNT(*)` SQLite queries.

#### Scenario: Estimating remaining time and disk consumption
- **WHEN** files are processed across varying modalities
- **THEN** the engine calculates average processing rates and updates estimated time remaining and projected database growth

#### Scenario: Lifecycle status state transitions
- **WHEN** the engine transitions through starting, syncing, indexing, paused, and idle states
- **THEN** current engine activity, processed file counters, and active filenames are published accurately

#### Scenario: Returning cached status while idle without querying SQLite
- **WHEN** `get_status()` is polled while the engine activity is `IDLE`
- **THEN** the service returns the cached `EngineStatus` snapshot without issuing `stats()` queries against the SQLite databases

### Requirement: Code Repository Root and Relative Title Resolution
When indexing code files, the indexing service MUST resolve the repository root by walking upward from the file to the nearest `.git` directory or code project root marker before falling back to the configured code root folder, and MUST format segment titles with the repository directory name followed by the relative file path (e.g., `semsearch/src/semsearch/services/searcher.py`).

#### Scenario: Nested git repository inside configured parent directory
- **WHEN** a code folder such as `~/Develop` contains multiple repositories (e.g., `~/Develop/semsearch/.git`)
- **THEN** the indexed code segment sets `repo` to `~/Develop/semsearch` and `title` to `semsearch/src/...`

### Requirement: Event-Driven Zero-Wakeup Idle Waiting
When no pending or partial files remain in any indexing queue, the indexing worker MUST transition to `IDLE`, invoke `shrink_memory()` on active databases, and block indefinitely on an event signal (`_work_event`) with zero periodic polling wakeups until new work is enqueued, synchronized, or the service is stopped.

#### Scenario: Blocking without CPU wakeups when queues are empty
- **WHEN** the indexing worker finishes processing all queued and partial files
- **THEN** the worker transitions to `IDLE`, shrinks SQLite memory on the general and code databases, and blocks on `_work_event` without polling

#### Scenario: Immediate wakeup upon file enqueue or folder sync
- **WHEN** `enqueue_file()`, `crawl_and_sync()`, or `stop()` is called while the worker is idle
- **THEN** `_work_event` is set and the worker thread wakes immediately to process queued work or terminate
