# Tasks

## 1. Storage & Database

- [x] 1.1 Implement `src/semsearch/core/database.py` with schema v5, file/segment operations, FTS5, and vector retrieval
- [x] 1.2 Implement unit tests in `tests/test_database.py` and verify with `.venv/bin/pytest tests/test_database.py`

## 2. Vector Engine

- [x] 2.1 Implement `src/semsearch/core/vectors.py` with L2 normalization, cosine similarity, float16 conversions, and Z-score normalization
- [x] 2.2 Implement unit tests in `tests/test_vectors.py` and verify with `.venv/bin/pytest tests/test_vectors.py`

## 3. Rules & Crawler Engine

- [x] 3.1 Implement `src/semsearch/core/rules.py` with exclusion, secret, project root, and dataset detection rules
- [x] 3.2 Implement `src/semsearch/core/crawler.py` with `crawl_folder` and `sync_candidates`
- [x] 3.3 Implement unit tests in `tests/test_crawler.py` and verify with `.venv/bin/pytest tests/test_crawler.py`

## 4. Verification & OpenSpec Validation

- [x] 4.1 Run full test suite with `.venv/bin/pytest -v`
- [x] 4.2 Validate OpenSpec change with `openspec validate phase-1-core-storage`
