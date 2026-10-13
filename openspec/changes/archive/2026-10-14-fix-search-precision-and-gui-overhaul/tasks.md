# Tasks

## 1. Core Storage, Vector Calibration & Multilingual Query Engine

- [x] 1.1 Update `src/semsearch/core/vectors.py` to remove the inverted `CALIBRATED_IMAGE_OFFSET` and accept an optional `sets_scale` boolean mask in `z_score_normalize`, and verify with unit tests in `tests/test_vectors.py`.
- [x] 1.2 Update `src/semsearch/core/database.py` to synchronize all SQLite operations with `threading.RLock()`, guard `fts_search` against malformed syntax, increase FTS5 `name` BM25 weight (`bm25(fts, 10.0, 1.0)`), and return `sets_scale` flags from `get_all_vectors()` (plus helper `get_file_segments`), verifying with `tests/test_database.py`.
- [x] 1.3 Update `src/semsearch/core/search_text.py` to add Spanish stopwords and kind-hint words (`STOPWORDS`, `KIND_WORDS`) and make `find_term_matches` diacritic-insensitive (`NFD` combining mark stripping), verifying with `tests/test_search_text.py`.
- [x] 1.4 Update `src/semsearch/core/rules.py` and `src/semsearch/core/crawler.py` to classify Spanish screenshot prefixes (`captura de pantalla`, `captura`), support repository docs/configs in code mode, enforce `excluded_folders` and `excluded_types` in `should_skip` and `crawl_folder`, and prune/reclassify files during `sync_candidates`, verifying with `tests/test_rules.py` and `tests/test_crawler.py`.

## 2. Extractors, Indexing Service & Hybrid Search Engine Precision

- [x] 2.1 Update `src/semsearch/extractors/code.py`, `src/semsearch/services/indexer.py`, and `src/semsearch/services/watcher.py` to fix `_chunk_lines` closing-brace block starts, emit 1-based `SegmentKind.CELLS` for notebooks, resolve `_code_place` by walking up to `.git`/project root markers with repo-prefixed titles, and propagate `excluded_folders`/`excluded_types` to crawling and watching, verifying with `tests/test_extractors_code.py`, `tests/test_indexer.py`, and `tests/test_watcher.py`.
- [x] 2.2 Update `src/semsearch/services/searcher.py` to support `prefix_last` and `kinds` parameters, pass `sets_scale` into `z_score_normalize`, compute lexical coverage across all candidate files, apply filename/BM25 rank bonuses, admit pure kind-hint queries (`not terms and hinted_kinds`), and add code definition ranking bonuses with segment-aligned `_resolve_code_place`, verifying with `tests/test_searcher.py`.

## 3. Desktop GUI Theme, Previews, Panel, Window & Settings Overhaul

- [x] 3.1 Overhaul `src/semsearch/gui/theme.py` (`build_qss`) to remove destructive universal `QWidget` font/background overrides and unscoped `QLineEdit`/`QListWidget::item` padding, and add properly scoped styles for windows, panels, cards, lists, splitters, spinboxes, menus, and tooltips, verifying with `tests/test_gui_theme.py`.
- [x] 3.2 Upgrade `src/semsearch/gui/preview.py` (`PreviewPane`) with scoped `MetadataCard` styling, dynamic viewport scaling for images and PDFs (`alpha=False`), real `ffmpeg` video frame extraction at matched timestamps, highlighted document passage previews, and `Open` / `Show in Files` action buttons, verifying with `tests/test_gui_preview.py`.
- [x] 3.3 Overhaul `src/semsearch/gui/panel.py` (`SearchPanel`, `ResultItemWidget`) to fix horizontal/vertical geometry clipping, add header status badge and shortcut footer bar, render place badges and elided snippets, deduplicate instant lexical matches, pass `prefix_last=True` to `SearchWorker`, and connect double-click activation, verifying with `tests/test_gui_panel.py`.
- [x] 3.4 Overhaul `src/semsearch/gui/window.py` (`SearchWindow`, `CardGridView`, `ResultCard`, `CodeListView`) with responsive column calculation on `resizeEvent`, card selection state, PDF/video card thumbnails, foldable "Weaker matches" section, accurate modality filter chips (including Screenshots), single-line code excerpts with keyboard navigation, empty/no-results states, and a bottom status bar, verifying with `tests/test_gui_window.py`.
- [x] 3.5 Update `src/semsearch/main.py`, `src/semsearch/gui/app.py`, `src/semsearch/gui/settings.py`, `src/semsearch/gui/onboarding.py`, `src/semsearch/gui/tray.py`, and `src/semsearch/gui/worker.py` so bare `semsearch` opens the main window, `SettingsDialog` layout and `settings_saved` live-reload work end-to-end, `OnboardingWizard` renders cleanly with folder removal buttons, and `SystemTrayManager` polls `index_service.get_status()`, verifying with `tests/test_gui_desktop.py` and `tests/test_main.py`.

## 4. End-to-End Verification & Live Database Validation

- [x] 4.1 Run the complete test suite (`.venv/bin/pytest`) and verify all unit, GUI, and E2E tests pass with zero inline imports across `src/semsearch/`.
- [x] 4.2 Synchronize and repair the user's live `~/.local/share/semsearch/index.sqlite` and `code.sqlite` databases and verify live multimodal and code search precision (`skull`, `calavera`, `juan ezquerro`, `captura de pantalla`, `screenshot`, `atomic explorer`, `code: SearchService`, `code: z_score_normalize`) and GUI rendering.

## Workflow follow-up

- Sync delta specs to main `openspec/specs/` and validate specs with `openspec validate --specs`.
- Archive the completed OpenSpec change.
