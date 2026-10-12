# Tasks

## 1. Search Engine & Hybrid Ranking

- [x] 1.1 Implement query routing (`code:` prefix detection and stripping, code DB vs general DB selection) in `src/semsearch/services/searcher.py`, verifying with unit tests.
- [x] 1.2 Implement query normalization, stopword filtering, and keyword coverage calculation in `src/semsearch/services/searcher.py`, verifying coverage boundary assertions.
- [x] 1.3 Implement lookup query detection (camelCase, snake_case, code symbols, serial identifiers) and dynamic lexical/semantic weight assignment in `src/semsearch/services/searcher.py`, verifying detection tests.
- [x] 1.4 Implement vector cosine similarity and cross-modality Z-score fusion with Ollama query embeddings in `src/semsearch/services/searcher.py`, verifying similarity ranking tests.
- [x] 1.5 Implement the DigUp ranking formula with HintBonus (filename matches, definition lines) and quality gating ($z \ge 2.0$ or $\text{coverage} \ge 0.5$) in `src/semsearch/services/searcher.py`, verifying gating tests.
- [x] 1.6 Implement weaker matches folding ($\text{score} < 0.5 \times \text{topScore}$) and `ResultRow` view model transformation in `src/semsearch/services/searcher.py`, verifying folding assertions.
- [x] 1.7 Implement test suite in `tests/test_searcher.py` and verify all tests pass with `.venv/bin/pytest tests/test_searcher.py`.

## 2. Desktop Actions & Deep Links

- [x] 2.1 Implement `open_file_default` invoking `xdg-open` in `src/semsearch/services/actions.py`, verifying process launch assertions.
- [x] 2.2 Implement `open_media_timestamp` with `mpv` and `vlc` timestamp argument formatting and CLI fallbacks in `src/semsearch/services/actions.py`, verifying command execution arguments.
- [x] 2.3 Implement `open_pdf_page` with `okular` and `evince` page jumping arguments in `src/semsearch/services/actions.py`, verifying page parameter formatting.
- [x] 2.4 Implement `open_code_editor` supporting VS Code, Cursor, Zed, Sublime Text, and JetBrains with line jump flags in `src/semsearch/services/actions.py`, verifying editor command patterns.
- [x] 2.5 Implement `reveal_in_file_manager` using DBus `org.freedesktop.FileManager1` and file manager CLI fallbacks (`dolphin`, `nautilus`, `nemo`) in `src/semsearch/services/actions.py`, verifying IPC/subprocess calls.
- [x] 2.6 Implement `copy_to_clipboard` with clipboard tooling fallbacks in `src/semsearch/services/actions.py`, verifying text copying behavior.
- [x] 2.7 Implement test suite in `tests/test_actions.py` and verify all tests pass with `.venv/bin/pytest tests/test_actions.py`.

## 3. Linux Power Scheduler & Policy

- [x] 3.1 Implement native Linux battery status check via `/sys/class/power_supply/` in `src/semsearch/services/scheduler.py`, verifying discharging and AC detection tests with mock sysfs paths.
- [x] 3.2 Implement operating hours schedule check supporting overnight ranges (e.g. 22:00 to 07:00) in `src/semsearch/services/scheduler.py`, verifying time window boundary tests.
- [x] 3.3 Implement `PowerScheduler` background loop coordinating pause/resume on `IndexService` in `src/semsearch/services/scheduler.py`, verifying pause event dispatching.
- [x] 3.4 Implement test suite in `tests/test_scheduler.py` and verify all tests pass with `.venv/bin/pytest tests/test_scheduler.py`.

## 4. Inotify Filesystem Watcher

- [x] 4.1 Implement `watchdog` event handler in `src/semsearch/services/watcher.py` filtering hidden files, excluded directories, and ignored extensions, verifying filter assertions.
- [x] 4.2 Implement differential debouncing (0.75s quiet window for general folders, 60s quiet window with 10m ceiling for code repositories) in `src/semsearch/services/watcher.py`, verifying debounce timer behavior.
- [x] 4.3 Implement file creation, modification, move, and deletion event dispatching to `IndexService` and `Database` in `src/semsearch/services/watcher.py`, verifying event handler dispatching.
- [x] 4.4 Implement test suite in `tests/test_watcher.py` and verify all tests pass with `.venv/bin/pytest tests/test_watcher.py`.

## 5. Indexing Pipeline & Background Service

- [x] 5.1 Implement `MeasuredCosts` throughput tracking, remaining time calculation, and disk space estimation in `src/semsearch/services/indexer.py`, verifying metric computations.
- [x] 5.2 Implement multi-tier priority queues (immediate new files <= 50, full backfill, code index, long file completion) in `src/semsearch/services/indexer.py`, verifying queue order assertions.
- [x] 5.3 Implement two-stage pipelining (background prepare/embedding worker decoupled from database commit worker) in `src/semsearch/services/indexer.py`, verifying pipeline flow tests.
- [x] 5.4 Implement partial file continuation (pages 1–30 initial pass, subsequent pages continuation) in `src/semsearch/services/indexer.py`, verifying state transitions and resumption.
- [x] 5.5 Implement `EngineStatus` lifecycle transitions and progress reporting in `src/semsearch/services/indexer.py`, verifying status updates.
- [x] 5.6 Implement test suite in `tests/test_indexer.py` and verify all tests pass with `.venv/bin/pytest tests/test_indexer.py`.

## 6. System Integration & Verification

- [x] 6.1 Validate OpenSpec change proposal strictly with `openspec validate phase-3-services-search --strict`.
- [x] 6.2 Run the full test suite with `.venv/bin/pytest -v` ensuring zero regressions across all core, extractor, and service modules.
