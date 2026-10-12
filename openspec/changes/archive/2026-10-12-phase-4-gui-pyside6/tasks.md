# Tasks

## 1. Visual Design System & Theme

- [x] 1.1 Implement color tokens, theme constants, and typography fonts in `src/semsearch/gui/theme.py`, verifying token definitions.
- [x] 1.2 Implement QSS stylesheets for search input, list views, cards, chips, scrollbars, and dialogs in `src/semsearch/gui/theme.py`, verifying QSS syntax and style rules.
- [x] 1.3 Implement unit tests in `tests/test_gui_theme.py` verifying theme constants and widget stylesheet application under offscreen Qt platform.

## 2. Dynamic System Tray Progress Ring

- [x] 2.1 Implement `render_tray_icon` dynamic QPainter circular progress ring and emblem states in `src/semsearch/gui/tray.py`, verifying pixel rendering and arc sweep calculations.
- [x] 2.2 Implement `SystemTrayManager` with context menu actions (status label, quick search, search window, pause/resume indexing toggle, settings, quit) in `src/semsearch/gui/tray.py`, verifying action triggers.
- [x] 2.3 Implement unit tests in `tests/test_gui_tray.py` verifying tray icon pixmaps, status updates, and menu actions.

## 3. Multimodal Preview Component

- [x] 3.1 Implement `ImagePreviewWidget` with aspect-ratio preserving pixmap scaling in `src/semsearch/gui/preview.py`, verifying image scaling and metadata display.
- [x] 3.2 Implement `PdfPreviewWidget` rendering pages with PyMuPDF and drawing yellow query term highlight boxes in `src/semsearch/gui/preview.py`, verifying bounding box highlights and page navigation.
- [x] 3.3 Implement `AudioPreviewWidget` with synthetic 64-bar waveform QPainter visualization and playback scrub controls in `src/semsearch/gui/preview.py`, verifying waveform painting and slider updates.
- [x] 3.4 Implement `VideoPreviewWidget` with thumbnail display, duration label, and timestamp jumping in `src/semsearch/gui/preview.py`, verifying video controls.
- [x] 3.5 Implement `CodePreviewWidget` with line number gutter, syntax styling, and definition line background highlight in `src/semsearch/gui/preview.py`, verifying gutter layout and highlight anchors.
- [x] 3.6 Implement `PreviewPane` stacked widget coordinating multimodal loaders in `src/semsearch/gui/preview.py`, verifying view switching across all modalities.
- [x] 3.7 Implement unit and widget tests in `tests/test_gui_preview.py` verifying all preview modes under offscreen Qt test environment.

## 4. Quick Search Panel

- [x] 4.1 Implement borderless, 18px rounded floating window with collapsed (56pt) and expanded (480pt) layout geometry in `src/semsearch/gui/panel.py`, verifying window flags and geometry transitions.
- [x] 4.2 Implement dual-cadence search execution (instant FTS5 lexical matching on keystroke + 120ms debounced semantic search) in `src/semsearch/gui/panel.py`, verifying timer debounce and search dispatching.
- [x] 4.3 Implement two-column results view (330px list + 418px preview pane) with kind category headers and folded weaker matches row in `src/semsearch/gui/panel.py`, verifying item grouping and unfolding toggle.
- [x] 4.4 Implement complete keyboard navigation (Up/Down navigation, Enter activation, Shift+Enter/Ctrl+Enter file manager reveal, Escape dismiss) in `src/semsearch/gui/panel.py`, verifying key press event handlers.
- [x] 4.5 Implement widget tests in `tests/test_gui_panel.py` verifying keyboard navigation, expansion behavior, weaker matches folding, and search debouncing.

## 5. Full Search Window

- [x] 5.1 Implement 1040x700 standalone window layout with search header, splitter, content area, and preview sidebar in `src/semsearch/gui/window.py`, verifying initial dimensions and minimum constraints.
- [x] 5.2 Implement kind chips filter bar with shortcut mappings `Ctrl+1` through `Ctrl+7` in `src/semsearch/gui/window.py`, verifying active chip state and category filtering.
- [x] 5.3 Implement `CardGridView` responsive scrolling flow grid for visual and document media in `src/semsearch/gui/window.py`, verifying card rendering and selection synchronization.
- [x] 5.4 Implement `CodeListView` structured table/list for code matches with jump to definition in `src/semsearch/gui/window.py`, verifying line jump activation.
- [x] 5.5 Implement desktop drag-and-drop export (`QDrag` with URI list) from cards and list items in `src/semsearch/gui/window.py`, verifying drag MIME data payload.
- [x] 5.6 Implement widget tests in `tests/test_gui_window.py` verifying kind chip switching, grid selection, and drag-and-drop MIME generation.

## 6. Settings Dialog & Model Filtering

- [x] 6.1 Implement `FoldersTab` with directory addition/removal and excluded paths management in `src/semsearch/gui/settings.py`, verifying config synchronization.
- [x] 6.2 Implement `CodeTab` with code search toggle, code roots, and editor picker dropdown in `src/semsearch/gui/settings.py`, verifying preference persistence.
- [x] 6.3 Implement `StorageModelTab` with Ollama URL test connection and dropdown restricted strictly to verified multimodal models (defaulting to `embeddinggemma-2`) along with database statistics in `src/semsearch/gui/settings.py`, verifying model list filtering.
- [x] 6.4 Implement `GeneralTab` with hotkey configuration, autostart desktop file management, and power/schedule settings in `src/semsearch/gui/settings.py`, verifying autostart file generation.
- [x] 6.5 Implement widget tests in `tests/test_gui_settings.py` verifying settings loading, model dropdown filtering, and configuration saving.

## 7. Onboarding Wizard

- [x] 7.1 Implement 5-step `OnboardingWizard` (Welcome, Folders, Multimodal Model, Code, Completion) in `src/semsearch/gui/onboarding.py`, verifying page sequence and step validation.
- [x] 7.2 Implement wizard completion hooks to persist `onboarded = True` and trigger initial indexing in `src/semsearch/gui/onboarding.py`, verifying configuration state transition.
- [x] 7.3 Implement widget tests in `tests/test_gui_onboarding.py` verifying onboarding step progression and completion callbacks.

## 8. Global Hotkey, IPC Application Shell & CLI Dispatcher

- [x] 8.1 Implement `HotkeyListener` capturing `Ctrl+Shift+Space` via `pynput` daemon thread and emitting Qt signals in `src/semsearch/gui/hotkey.py`, verifying signal emission.
- [x] 8.2 Implement `QLocalServer`/`QLocalSocket` single-instance lock and IPC activation protocol in `src/semsearch/gui/app.py`, verifying secondary process forwarding and exit.
- [x] 8.3 Implement `SemSearchApp` coordinating widget lifecycles, service initialization, onboarding check, and panel visibility toggling in `src/semsearch/gui/app.py`, verifying app state management.
- [x] 8.4 Implement CLI dispatcher supporting `--panel`, `--window`, `--settings`, and `--daemon` arguments in `src/semsearch/main.py`, verifying argument parsing and IPC forwarding.
- [x] 8.5 Implement integration tests in `tests/test_gui_app.py` verifying single-instance locking, IPC message dispatching, and CLI routing.

## 9. Full System Integration & Spec Validation

- [x] 9.1 Validate OpenSpec change proposal strictly with `openspec validate phase-4-gui-pyside6 --strict`.
- [x] 9.2 Run the full test suite with `.venv/bin/pytest -v` ensuring zero regressions across all core, extractor, service, and GUI modules.
