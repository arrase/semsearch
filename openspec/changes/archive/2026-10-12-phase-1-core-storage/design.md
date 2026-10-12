# Design

## Context

Phase 1 establishes the foundational storage, vector calculation, and file crawler engine for SemSearch. It mirrors DigUp's architecture in Python while preserving radical simplicity (KISS principle), top-level PEP 8 imports, and zero defensive bloat.

## Goals / Non-Goals

**Goals:**
- Provide SQLite WAL database management for both `index.sqlite` and `code.sqlite` with schema v5 and FTS5.
- Store file records, segment pieces, and 768-dimensional float16 embedding vectors with cascading deletion.
- Implement high-performance vector math in NumPy (L2 normalization, cosine similarity, float16 conversions, modality-aware Z-score normalization).
- Implement file classification rules for secrets, skipped folders, code project roots, and machine-named dataset folders.
- Implement directory crawling via `os.scandir` and database reconciliation detecting additions, changes, moves by inode, and deletions.

**Non-Goals:**
- Calling Ollama APIs or running model inference (handled in Phase 2).
- PDF, DOCX, or image extraction logic (handled in Phase 2).
- Introducing any OCR libraries (visual data is directly embedded via multimodal models).
- GUI widgets or presentation layers.

## Decisions

- **SQLite WAL Mode & Schema v5**: Use standard `sqlite3` with WAL journal mode, `PRAGMA foreign_keys = ON`, `PRAGMA synchronous = NORMAL`, and `PRAGMA user_version = 5`. FTS5 is integrated with tokenizer `porter unicode61 remove_diacritics 2 categories 'L* N* Co M*'`.
- **FTS Row Synchronization**: `fts.rowid` is linked to `segments.id`. A `name` segment is created on file insertion for path/name queries. File moves update the path and FTS name row without re-embedding. Deletions cascade to segments and delete linked FTS rows.
- **NumPy for Vector Math**: 768-dimensional embeddings are stored as float16 BLOBs (1536 bytes) and converted to NumPy arrays. Dot products compute cosine similarities on normalized vectors.
- **Modality Z-Score Normalization**: For each modality with >= 20 items, compute mean $\mu_m$; otherwise use global mean. Mean-center scores and scale by $\max(\text{std}(c), 0.02)$.
- **Inode-Based Move Detection**: When a candidate has a new path but matches `(device, inode)` of an existing record whose old path no longer exists, `move_file` is invoked instead of re-embedding.

## Risks / Trade-offs

- *Cross-device file moves*: Inodes change across filesystem boundaries. → Treated as deletion and new insertion, which is standard filesystem behavior.
- *FTS syntax errors*: Arbitrary search queries can contain special syntax characters. → Keywords are tokenized into safe alphanumeric quoted patterns.
