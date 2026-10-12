# Proposal

## Why

SemSearch requires a persistent storage layer, vector mathematics engine, and filesystem crawler to index desktop files and perform fast multimodal semantic and keyword search.

## What Changes

- Implement `src/semsearch/core/database.py` with SQLite WAL mode, schema v5, file and segment management, cascading deletion, and FTS5 keyword indexing.
- Implement `src/semsearch/core/vectors.py` with L2 normalization, float16 conversions, cosine similarity, and modality-aware Z-score normalization.
- Implement `src/semsearch/core/rules.py` with filtering rules for secrets, skipped folders, code project roots, and dataset folders.
- Implement `src/semsearch/core/crawler.py` with recursive scanning via `os.scandir` and database state reconciliation (added, changed, moved, deleted).
- Add unit tests in `tests/test_database.py`, `tests/test_vectors.py`, and `tests/test_crawler.py`.

## Capabilities

### New Capabilities
- `core-storage`: SQLite WAL storage management, schema v5, file and segment persistence, cascading deletions, FTS5 full-text search indexing, and stats.
- `vector-engine`: Vector normalization (L2), cosine similarity, float16 serialization, and modality-aware Z-score normalization.
- `crawler-engine`: Filesystem crawling, exclusion/secret rules, and DB sync reconciliation.

### Modified Capabilities
None.

## Impact
- Establishes core storage, vector computation, and file discovery engines.
- Directly supports subsequent extraction, indexing pipelines, and search services.
