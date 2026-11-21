# search-panel Specification

## Purpose
Provides a fast, frameless, keyboard-driven floating quick search panel (`748px` wide) with instant lexical search, debounced semantic ranking, selection preservation, Drag & Drop, Quick Actions menu, and a modular asynchronous multimodal preview package.

## Requirements

### Requirement: Floating Borderless Geometry (`748px`), Multi-Monitor Positioning, and Contrast
The quick search panel MUST render as a frameless floating window (`PANEL_WIDTH = 748`, composed of `LIST_WIDTH = 328` and `PREVIEW_WIDTH = 416`, `PANEL_RADIUS = 18`, `PANEL_HEIGHT_COLLAPSED = 56`, `PANEL_HEIGHT_EXPANDED = 480`) centered in the upper third (`16%` vertical offset) of the monitor currently containing the mouse cursor (`QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()`). Expanded height MUST be clamped to `screen.availableGeometry().height() - 40` for small or HiDPI displays. Muted text tokens (`ThemeColors.TEXT_MUTED = "#94A3B8"`) MUST meet WCAG AA contrast on dark surfaces, items MUST expose `Qt.ItemDataRole.AccessibleTextRole`, and RTL layouts MUST flip the list divider border (`border-left` vs `border-right`) and weaker-matches arrow (`◂` vs `▸`).

#### Scenario: Centering on active cursor monitor and clamping height
- **WHEN** the quick search panel opens on a multi-monitor or small HiDPI display
- **THEN** it positions horizontally centered at `748px` (`328px` list + `416px` preview) on the cursor's active screen and clamps expanded height to stay within `availableGeometry()`

### Requirement: Dual-Cadence Search, `prefix_last=True`, and Selection Preservation
Upon typing, `SearchPanel` MUST delegate immediate lexical matching to `SearchService.lexical_search(query, limit=50)` (without direct SQLite access) and schedule a 120ms debounced `SearchWorker` executing `search_service.search(..., prefix_last=True)` serialized by `_search_lock`. When asynchronous semantic results arrive or when weaker matches are expanded/collapsed, `_populate_list()` MUST preserve the user's currently selected `hit.path` if it remains present in the updated list instead of jumping back to row 0.

#### Scenario: Preserving user selection when async semantic results arrive
- **WHEN** the user navigates down to a specific result during instant lexical display and the 120ms semantic `SearchWorker` subsequently emits updated results containing the same `hit.path`
- **THEN** the panel restores selection to that `hit.path` rather than resetting selection to the first item

#### Scenario: Obsolete SearchWorker skipping and safe thread retention
- **WHEN** rapid typing spawns multiple `SearchWorker` threads while an earlier query is waiting on Ollama
- **THEN** active workers remain referenced in `_active_workers` until `finished` triggers `deleteLater()`, and cancelled intermediate workers exit immediately after acquiring `_search_lock` without calling `search_service.search()`

### Requirement: Full Keyboard Navigation, Quick Actions Menu, and Drag & Drop
The panel MUST support:
- `↑` / `↓`, `PageUp` / `PageDown` (step 5), and `Home` / `End` navigation across selectable rows.
- `Enter`: activate selected hit via `execute_hit_action(hit, self.config)` and hide panel (or toggle weaker matches).
- `Shift+Enter` / `Ctrl+Enter`: reveal file via `reveal_in_file_manager(hit.path)` and hide panel.
- `Ctrl+C`: allow native Qt text copy when `search_input.hasSelectedText()` is `True`; otherwise copy selected file via `copy_file_to_clipboard(hit.path)`.
- `Tab` or `Ctrl+K`: open the Quick Actions `QMenu` (`Open`, `Show in Files`, `Copy File`, `Copy Path`, `Copy Excerpt`, `Open Terminal Here`) without triggering auto-dismiss.
- `Alt+←` / `Alt+→`: navigate previous/next page when PDF preview is active.
- `Esc`: clear non-empty input (collapsing panel) or hide panel if empty.
- Drag & Drop (`DraggableResultList`): initiate `QDrag` (`CopyAction`) carrying `QUrl.fromLocalFile(hit.path)` and path text.
- Footer hint displaying `↵ Open    Shift+↵ Show in Files    Ctrl+C Copy    Tab Actions    Esc Dismiss`.

#### Scenario: Respecting selected search text on Ctrl+C
- **WHEN** the user highlights text inside `search_input` and presses `Ctrl+C`
- **THEN** the event passes through to `QLineEdit` to copy the selected query text instead of copying the highlighted result file

#### Scenario: Opening Quick Actions menu and dragging a result
- **WHEN** the user presses `Tab` or `Ctrl+K` on a result, or drags a result row to another desktop application
- **THEN** the Quick Actions menu displays file/clipboard/terminal actions without dismissing the panel, and dragging exports `QUrl.fromLocalFile(hit.path)`

### Requirement: Modular Asynchronous Multimodal Preview Package (`gui/preview/`)
The preview pane MUST be structured as the modular `semsearch.gui.preview` package (`__init__.py`, `loader.py`, `metadata.py`, `image_view.py`, `pdf_view.py`, `media_view.py`, `code_view.py`) coordinated by `PreviewLoader` (`QThreadPool`, max 2 threads) using per-channel request IDs to discard stale results:
- **Disk Thumbnail Cache**: Video frames extracted via `ffmpeg` are cached atomically as PNGs under `~/.cache/semsearch/thumbs/` (max 200 entries, pruned by `mtime`).
- **ImagePreviewWidget**: Scales rasters via `QImageReader` with HiDPI `devicePixelRatioF()`, rasterizes `.svg` via PyMuPDF, plays animated `.gif` via `QMovie`, and loads files `> 512 KB` asynchronously.
- **PdfPreviewWidget**: Renders target page at `1.6 * dpr` zoom with yellow term highlights, supports `prev_page()` / `next_page()` (`Alt+←`/`Alt+→`), and shows controlled error states for missing, encrypted, or corrupt PDFs.
- **AudioPreviewWidget & VideoPreviewWidget**: Provide real in-panel `QMediaPlayer` + `QAudioOutput` playback positioned at `hit.loc`, real duration from `hit.info["duration"]` / `probe_media_duration()` / `durationChanged`, real 64-bar RMS amplitude envelope (`WaveformWidget`) via `ffmpeg`, and debounced (150ms) async video frame scrubbing.
- **CodePreviewWidget**: Renders `CodeEditor` with `LineNumberGutter`, `CodeSyntaxHighlighter`, UTF-16-safe diacritic-insensitive match highlights capped at `MAX_HIGHLIGHT_SELECTIONS = 200`, `256 KB` (`262_144` bytes) file truncation (`async_load` for files `> 128 KB`), line wrapping + excerpt scrolling for document/semantic matches without target line.
- **MetadataCard**: Clicking `Open` executes `execute_hit_action(hit, config)` preserving deep-link jumps (PDF page, media timestamp, code line) and emits `action_triggered` to dismiss the panel.

#### Scenario: Asynchronous preview switching without blocking UI thread
- **WHEN** the user rapidly arrows through large images, videos, audio clips, PDFs, and large code files
- **THEN** `PreviewLoader` cancels stale channel requests, serves cached frames from `~/.cache/semsearch/thumbs/`, truncates text previews at 256 KB, and keeps UI navigation responsive

#### Scenario: In-panel audio playback and deep-link MetadataCard Open
- **WHEN** the user clicks Play on an audio preview or clicks `Open` in `MetadataCard` on a PDF/code/media hit
- **THEN** `AudioPreviewWidget` plays audio at `hit.loc` via `QMediaPlayer` + `QAudioOutput` with its real RMS waveform, and `MetadataCard.Open` invokes `execute_hit_action(hit, config)` jumping to the exact page, line, or timestamp

### Requirement: IME-Safe Focus Loss Dismissal, Preloading, and Clean Shutdown
`WindowDeactivate` MUST dismiss `SearchPanel` only when `not self._menu_open and QApplication.activePopupWidget() is None` so Wayland/X11 IME candidate popups (`IBus`/`Fcitx5`) do not prematurely hide the panel. `show_panel()` MUST spawn at most one daemon thread calling `ollama.preload(model)`. `hide_panel()` MUST stop workers, clear results/preview, collapse geometry, and call `shrink_memory()` on active databases without unloading the model. `shutdown()` MUST stop and join all active `SearchWorker`s and call `preview_pane.shutdown()`.

#### Scenario: Preserving panel visibility while IME popup or Quick Actions menu is active
- **WHEN** an IME candidate popup or the Quick Actions menu temporarily activates while the user is typing or choosing an action
- **THEN** `SearchPanel` remains open and retains query state
