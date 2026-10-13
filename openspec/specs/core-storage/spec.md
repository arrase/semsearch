# core-storage Specification

## Purpose
The core-storage capability provides SQLite WAL database management, schema v5 enforcement, file and segment persistence, cascading deletions, and full-text keyword indexing.

## Requirements

### Requirement: Database Schema and Connection Management
The storage engine MUST initialize SQLite databases using WAL journal mode, synchronous normal, foreign key constraints, and user version 5, and MUST serialize concurrent thread access across connection operations using a reentrant lock (`threading.RLock`).

#### Scenario: Database initialization
- **WHEN** a Database instance is opened for an index path
- **THEN** WAL mode, foreign keys, and tables `meta`, `files`, `segments`, and `fts` are created with correct schemas and indexes

#### Scenario: Thread-safe concurrent database operations
- **WHEN** background indexing workers, filesystem watchers, and search threads invoke methods on the same `Database` instance concurrently
- **THEN** all SQLite cursor executions and transactions are synchronized by the database's reentrant lock without raising `sqlite3.InterfaceError`, `sqlite3.ProgrammingError`, or `SystemError`

### Requirement: File Lifecycle and Path Move Operations
The storage engine MUST support inserting candidate files, requeuing files upon modification, moving files by updating paths and FTS without mutating segments, and deleting files with cascading removal.

#### Scenario: Insert and move file
- **WHEN** a candidate file is inserted and subsequently moved to a new path
- **THEN** the file record and its FTS name row are updated without altering its segment IDs or embedding vectors

#### Scenario: Delete file with cascading removal
- **WHEN** a file record is deleted from the database
- **THEN** all associated segments and FTS virtual table rows are completely removed

### Requirement: Segment Persistence and Vector Retrieval
The storage engine MUST store searchable segments with optional embedding vectors and provide batch retrieval of all stored vectors along with per-segment scale-eligibility flags (`sets_scale`) so multi-segment files do not skew Z-score calibration.

#### Scenario: Retrieve stacked vectors
- **WHEN** segments with non-null embedding vectors are stored in the database
- **THEN** `get_all_vectors` returns the corresponding segment IDs, file IDs, modalities, stacked vectors matrix, and `sets_scale` boolean mask

### Requirement: Full-Text Search and Database Statistics
The storage engine MUST support BM25-ranked full-text keyword searches across names and content bodies with boosted filename weight (`bm25(fts, 10.0, 1.0)`), handle malformed FTS queries safely without crashing, and report summary counts.

#### Scenario: FTS keyword search
- **WHEN** an FTS query matching indexed text is executed
- **THEN** matching rows are returned ranked by relevance with filename matches weighted 10x higher than body matches
