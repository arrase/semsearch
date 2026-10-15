# indexing-service Specification

## MODIFIED Requirements

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

## ADDED Requirements

### Requirement: Event-Driven Zero-Wakeup Idle Waiting
When no pending or partial files remain in any indexing queue, the indexing worker MUST transition to `IDLE`, invoke `shrink_memory()` on active databases, and block indefinitely on an event signal (`_work_event`) with zero periodic polling wakeups until new work is enqueued, synchronized, or the service is stopped.

#### Scenario: Blocking without CPU wakeups when queues are empty
- **WHEN** the indexing worker finishes processing all queued and partial files
- **THEN** the worker transitions to `IDLE`, shrinks SQLite memory on the general and code databases, and blocks on `_work_event` without polling

#### Scenario: Immediate wakeup upon file enqueue or folder sync
- **WHEN** `enqueue_file()`, `crawl_and_sync()`, or `stop()` is called while the worker is idle
- **THEN** `_work_event` is set and the worker thread wakes immediately to process queued work or terminate
