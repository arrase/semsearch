# Design

## Context

`semsearch` is a Linux desktop multimodal semantic search engine built with Python, PySide6, SQLite (WAL + FTS5), and Ollama (`embeddinggemma-2`), modeled after `DigUp`. See `proposal.md` for problem motivation. All changes must adhere strictly to PEP 8 top-level imports (zero inline imports) and minimal, linear KISS architecture.

## Goals / Non-Goals

**Goals:**
- Deliver high-precision cross-modal semantic and lexical ranking across text, PDFs, images, screenshots, audio, video, and source code in both English and Spanish.
- Guarantee thread-safe SQLite access across concurrent search, indexing, and filesystem watcher threads.
- Enforce user-configured folder and file-type exclusions end-to-end.
- Make the PySide6 desktop GUI (`SearchWindow`, `SearchPanel`, `PreviewPane`, `SettingsDialog`, `OnboardingWizard`, `SystemTrayManager`) visually polished, responsive, unclipped, and functionally complete on Linux X11/Wayland.

**Non-Goals:**
- Changing the SQLite schema version (`USER_VERSION = 5`) or migrating away from SQLite FTS5 / float16 vector blobs.
- Changing the embedding model family (`embeddinggemma-2` via Ollama `/api/embed`).

## Decisions

### 1. Scale-Eligible Z-Score Calibration (`sets_scale`) without Artificial Image Offset
- **Decision**: Remove `CALIBRATED_IMAGE_OFFSET = 0.08` from `z_score_normalize` and compute `sets_scale` per segment in `Database.get_all_vectors()` (or vector loader). Specifically, only the initial segment(s) per file (`loc == 0` or first segment per `(file_id, modality)` plus up to `PDF_PAGE_STEP = 30` pages when `pages <= 50`) set the modality mean and global standard deviation (`std = max(global_std, 0.02)`). Modalities with `< 20` scale-eligible vectors use the global scale-eligible mean directly.
- **Rationale**: In `embeddinggemma-2`, raw cosine similarities for image embeddings against text queries are lower than text-to-text cosines (~0.25 vs ~0.35). Adding `+0.08` to the text mean when centering images subtracted an extra `0.08` from image cosines, suppressing valid visual matches (`skull.jpg`). Meanwhile, allowing all 31 chunks of a single markdown file or 23 segments of a single video into `sets_scale` caused one file to dominate the mean/std and produce `z > 2.5` false positives on unrelated queries. Capping scale contribution per file matches `DigUp`'s `rowSetsScale` principle and stabilizes small and large corpora alike.

### 2. Hybrid Lexical & Code Definition Scoring
- **Decision**:
  - Weight the `name` column 10x over `body` in FTS5 (`bm25(fts, 10.0, 1.0)`).
  - Evaluate per-term FTS coverage across all candidate files (union of semantic candidates and all files matching the `OR` pattern query).
  - Add a lexical rank / filename match bonus (`lexical_bonus`) when query terms match the file's stem/name or rank at the top of BM25 results, ensuring `Autorización Juan Ezquerro.pdf` ranks #1 ahead of incidental body mentions.
  - When a query consists solely of kind-hint words (`not terms and hinted_kinds`), admit files whose `kind in hinted_kinds` directly through the quality gate.
  - In code mode (`is_code_mode` or `FileKind.CODE`), check `find_definition` across candidate files when an identifier is present and add a definition bonus (`+2.5` for strength 2, `+1.25` for strength 1) while resolving `(segment_id, focus_line)` to the exact segment containing `focus_line`.
- **Rationale**: Combines semantic recall with deterministic lexical and symbol-definition precision.

### 3. Multilingual (EN/ES) Query Normalization & Diacritic-Insensitive Snippets
- **Decision**: Expand `STOPWORDS` and `KIND_WORDS` in `search_text.py` with Spanish stopwords and modality terms, add `"captura de pantalla"` and `"captura"` to `SCREENSHOT_PREFIXES` in `rules.py`, and strip Unicode `Mn` combining marks after `NFD` decomposition inside `find_term_matches` while preserving 1-to-1 character indices with the original text.
- **Rationale**: Ensures Spanish queries (`captura de pantalla`, `informacion fiscal`, `autorizacion`) extract clean terms, classify Spanish screenshots properly, and highlight accented text (`INFORMACIÓN`, `Autorización`) even when queried without accents.

### 4. Reentrant SQLite Locking (`threading.RLock`)
- **Decision**: Equip `Database` with `self._lock = threading.RLock()` and wrap all database read/write methods and transactions under `with self._lock:`. Wrap `fts_search` in `try...except sqlite3.Error: return []`.
- **Rationale**: `IndexService`, `FileSystemWatcher`, and `SearchWorker` share `Database` instances across threads. `sqlite3` connections with `check_same_thread=False` are not thread-safe without external serialization in CPython and caused `SystemError` in `code.sqlite`.

### 5. Scoped QSS Architecture & Responsive PySide6 Layouts
- **Decision**:
  - Replace the universal `QWidget { font-family: ...; font-size: 13px; background: transparent; }` rule in `theme.py` with targeted selectors (`QMainWindow`, `QDialog`, `QFrame#panelContainer`, etc.) so programmatic `setFont()` calls and native dialog/menu/spinbox backgrounds work undisturbed.
  - Scope `QLineEdit` padding and `QListWidget::item` padding so `QTimeEdit` spinboxes and custom `setItemWidget` rows (`ResultItemWidget`) are never clipped.
  - Fix `SearchPanel` width (`780px` with `330px` list + `1px` divider + `425px` preview pane) and collapsed height (`64px`), adding the header status pill and `30px` shortcut footer bar matching `DigUp`.
  - Make `CardGridView` compute `columns = max(1, (viewport_width - margins) // (CARD_WIDTH + spacing))` dynamically on `resizeEvent`, and add a collapsible `"Weaker matches"` section in `SearchWindow`.
  - Upgrade `PreviewPane` to scale images/PDFs to the viewport, extract real video frames at `loc` via `ffmpeg`, highlight query matches in document chunk passages, and provide `Open` and `Show in Files` buttons.

## Risks / Trade-offs

- **[Risk] `ffmpeg` subprocess latency during rapid list navigation on video hits** → Mitigation: Run `ffmpeg -ss <loc> -i <path> -frames:v 1` with a short timeout (`3s`) and cache extracted `QPixmap` frames by `(path, loc)` in memory.
- **[Risk] Existing user databases (`index.sqlite`, `code.sqlite`) contain stale classifications or excluded `.txt` files** → Mitigation: Enhance `sync_candidates` to prune already-indexed files that match `should_skip` (such as `excluded_types` / `excluded_folders`) and update `kind` when `classify_file` changes (e.g. `image` → `screenshot`).
