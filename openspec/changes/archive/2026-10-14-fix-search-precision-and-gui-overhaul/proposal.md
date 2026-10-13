# Proposal

## Why

The `semsearch` desktop application currently suffers from two critical defects:
1. **Inaccurate Search Precision & Engine Flaws**: Cross-modal Z-score normalization applies an inverted `+0.08` offset to image embeddings and lacks per-file scale capping (`sets_scale`), allowing single multi-chunk documents or long videos to dominate the similarity scale and inject false positives. Furthermore, BM25 filename matches are underweighted relative to body mentions, Spanish/multilingual stopwords and diacritics are unhandled, pure kind-hint queries (`screenshot`, `captura de pantalla`) fail to match target files, code definition hits lack ranking priority, user-configured exclusions (`excluded_folders`, `excluded_types`) are ignored by the crawler, and concurrent SQLite access across indexing/watcher/search threads lacks synchronization.
2. **Broken PySide6 Desktop GUI**: Global QSS rules (`QWidget { font-size: 13px; background: transparent; }`, unscoped `QLineEdit` and `QListWidget::item` padding) crush custom widgets, strip backgrounds, override programmatic fonts, and clip text. Both the floating `SearchPanel` and standalone `SearchWindow` suffer from geometry overflow, missing header/footer bars, unresponsive fixed-column grids, broken multimodal previews (hardcoded tiny thumbnails, fake video placeholders, unhighlighted document chunks), disconnected settings persistence, and launching `semsearch` without CLI flags silently enters background daemon mode instead of showing the main window.

## What Changes

- **Search Precision & Vector Calibration (`vector-engine`, `search-engine`)**:
  - Remove the inverted `CALIBRATED_IMAGE_OFFSET` in `z_score_normalize` and introduce `sets_scale` segment eligibility so multi-chunk documents, long PDFs, and multi-segment videos do not skew modality means or collapse global standard deviation.
  - Increase FTS5 `name` column BM25 weighting (`bm25(fts, 10.0, 1.0)`), compute lexical term coverage across all semantic and lexical candidate files, add a lexical rank/filename bonus, support pure kind-hint queries (`not terms and hinted_kinds`), and add code definition ranking bonuses with accurate segment-aligned line resolution.
  - Add multilingual (English + Spanish) stopwords, kind hint terms, screenshot filename prefixes (`captura de pantalla`), and diacritic-insensitive (`NFD` combining mark stripping) snippet matching and highlighting.
- **Storage Thread Safety & Crawler Exclusions (`core-storage`, `crawler-engine`, `indexing-service`)**:
  - Synchronize `Database` operations with a reentrant lock (`threading.RLock`) and guard `fts_search` against malformed FTS5 syntax errors.
  - Enforce `config.excluded_folders` and `config.excluded_types` across `should_skip`, `crawl_folder`, `sync_candidates`, `IndexService`, and `FileSystemWatcher`.
  - Fix code repository root detection (`_code_place`) to walk up to `.git`/project root markers before falling back to configured root folders, and include the repository directory name in code segment titles.
- **Desktop GUI, Search Window, and Quick Search Panel Overhaul (`desktop-gui`, `search-window`, `search-panel`)**:
  - Default `semsearch` CLI invocation (without `--daemon` or `--panel`) to `SHOW_WINDOW`.
  - Overhaul `build_qss()` in `theme.py` to remove destructive universal `QWidget` font/background overrides and unscoped item/input padding, styling all window, panel, card, list, splitter, spinbox, and dialog components cleanly.
  - Redesign `SearchPanel` geometry, header, grouped result list (with thumbnails, place badges, snippets, deduplicated instant lexical hits, `prefix_last=True` semantic queries), synchronized preview pane, and shortcut footer bar.
  - Redesign `SearchWindow` with responsive `CardGridView` column reflow, visual card selection states, PDF/video thumbnails, place badges, foldable "Weaker matches" section, accurate modality filter chips (including Screenshots), empty/no-results states, and a live indexing status footer bar.
  - Upgrade `PreviewPane` with dynamic image/PDF scaling, real `ffmpeg` video frame extraction at matched timestamps, highlighted document passage previews, and `Open` / `Show in File Manager` action buttons.
  - Wire `SettingsDialog` changes (`settings_saved` signal) to live-reload configuration, watcher descriptors, hotkeys, and indexers, and fix `SystemTrayManager` live status polling.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `vector-engine`: Calibrate modality Z-score normalization using `sets_scale` segment eligibility and global mean fallback without artificial positive image offset.
- `search-engine`: Add multilingual query parsing, diacritic-insensitive snippet matching, lexical rank/filename boosting, pure kind-hint query matching, and code definition ranking bonuses.
- `core-storage`: Enforce thread-safe SQLite database access via reentrant locking, increase FTS5 filename BM25 weight, and expose segment scale eligibility (`sets_scale`) in vector retrieval.
- `crawler-engine`: Enforce user-configured `excluded_folders` and `excluded_types` during crawling and synchronization, and support Spanish screenshot prefixes and code repository documentation classification.
- `indexing-service`: Propagate user exclusions to background crawling/watching and resolve code repository roots from `.git`/project markers with repo-prefixed titles.
- `desktop-gui`: Default bare `semsearch` invocation to showing the main window, live-apply saved settings across services, and poll live indexing progress in the system tray.
- `search-window`: Provide responsive multi-column card grid reflow, foldable weaker matches, screenshot/modality chip filtering, empty/status states, and enhanced multimodal preview actions.
- `search-panel`: Fix panel geometry without horizontal clipping, add header status badge and shortcut footer, deduplicate instant lexical matches, and run prefix-enabled hybrid search.

## Impact

- **Core & Services**: `src/semsearch/core/vectors.py`, `src/semsearch/core/database.py`, `src/semsearch/core/search_text.py`, `src/semsearch/core/rules.py`, `src/semsearch/core/crawler.py`, `src/semsearch/extractors/code.py`, `src/semsearch/services/searcher.py`, `src/semsearch/services/indexer.py`, `src/semsearch/services/watcher.py`.
- **GUI & Entrypoint**: `src/semsearch/main.py`, `src/semsearch/gui/theme.py`, `src/semsearch/gui/app.py`, `src/semsearch/gui/panel.py`, `src/semsearch/gui/window.py`, `src/semsearch/gui/preview.py`, `src/semsearch/gui/settings.py`, `src/semsearch/gui/onboarding.py`, `src/semsearch/gui/tray.py`, `src/semsearch/gui/worker.py`.
- **Tests**: Unit, GUI, and E2E test suites in `tests/`.
