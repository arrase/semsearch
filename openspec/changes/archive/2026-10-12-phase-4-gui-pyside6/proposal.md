# Proposal

## Why

SemSearch requires a modern, responsive Linux desktop graphical interface to deliver lightning-fast keyboard-first search, rich multimodal file previews, and intuitive desktop controls. Replicating the sleek user experience of DigUp, Phase 4 delivers a floating Spotlight/Raycast-style quick search panel, an expansive standalone search window with faceted filtering and card layouts, a system tray monitor with live indexing feedback, and comprehensive settings and onboarding workflows using PySide6 (Qt 6).

## What Changes

- Implement `src/semsearch/gui/theme.py` establishing the visual design system: translucent dark palette, brand gradients (`Brand.gradient`), accent colors (amber, leaf), standardized fonts, and Qt stylesheets (QSS) for panels, cards, lists, and inputs.
- Implement `src/semsearch/gui/panel.py` providing the `SearchPanel` floating window: borderless, 18px rounded corners, dynamic vertical expansion from 56pt (collapsed input) to 480pt (results view), two-column layout (330px result list + 418px preview pane), instant FTS5 lexical matching combined with 120ms debounced semantic search, kind headers, folded weaker matches row, and full arrow/Enter/Escape keyboard navigation.
- Implement `src/semsearch/gui/preview.py` providing the `PreviewPane` supporting rich native previews: image viewer with aspect-ratio scaling, PDF page renderer with highlighted search terms, audio player with synthetic waveform bars and playback scrub, video player with scrub bar and thumbnail playback, and syntax-highlighted code viewer with gutter line numbering and target definition highlighting.
- Implement `src/semsearch/gui/window.py` providing the `SearchWindow` standalone 1040x700 search interface: kind chip selector bar (`All`, `PDF`, `Images`, `Audio`, `Video`, `Code`, `Docs` bound to `Ctrl+1`..`Ctrl+7`), adaptive card grid for visual media, syntax list for code results, full-height preview sidebar, and drag & drop file export.
- Implement `src/semsearch/gui/tray.py` providing the `SystemTrayManager` with a dynamic circular progress ring rendered via `QPainter` reflecting live indexing percentage, pause/resume toggle, status tooltip, and context menu.
- Implement `src/semsearch/gui/settings.py` providing `SettingsDialog` with a 4-tab `QTabWidget`: Folders tab (indexed/excluded paths), Code tab (code index toggle, code roots, editor picker), Storage & Model tab (Ollama host endpoint and filtered dropdown listing ONLY verified multimodal embedding models defaulting to `embeddinggemma-2`), and General tab (global hotkey assignment, system autostart toggle).
- Implement `src/semsearch/gui/onboarding.py` providing `OnboardingWizard` with a 5-step modal workflow (Welcome, Directory Selection, Multimodal Model Setup, Code Indexing Setup, Completion & Hotkey Overview) triggered on initial application launch.
- Implement `src/semsearch/gui/hotkey.py` providing `HotkeyListener` capturing the global shortcut `Ctrl+Shift+Space` across X11 and Wayland desktop environments via `pynput` with thread-safe Qt signal emission.
- Implement `src/semsearch/gui/app.py` providing `SemSearchApp` coordinating single-instance lock via `QLocalServer`/`QLocalSocket`, IPC activation message passing, lifecycle management of indexing/watcher services, and visibility toggling of search widgets.
- Refactor `src/semsearch/main.py` into a CLI dispatcher supporting `--panel`, `--window`, `--settings`, `--daemon`, and default interactive GUI launch flags.
- Add comprehensive test suites for GUI models, preview renderers, settings validation, IPC single-instance locks, and hotkey signaling.

## Capabilities

### New Capabilities
- `search-panel`: Floating borderless quick search panel with instant FTS5, 120ms debounced semantic search, two-column layout (330px list + 418px preview), kind headers, folded weaker matches, and full keyboard navigation.
- `search-window`: Full 1040x700 standalone search window with kind chip filters (`Ctrl+1`..`Ctrl+7`), adaptive card grid, code syntax view, preview sidebar, and drag & drop export.
- `desktop-gui`: Desktop application shell providing QLocalServer single-instance lock, system tray icon with dynamic QPainter progress ring, settings dialog with Ollama multimodal model filtering, 5-step onboarding wizard, and global hotkey invocation.

### Modified Capabilities
None.

## Impact

- Introduces new GUI module `src/semsearch/gui/` containing: `theme.py`, `panel.py`, `preview.py`, `window.py`, `tray.py`, `settings.py`, `onboarding.py`, `hotkey.py`, and `app.py`.
- Updates `src/semsearch/main.py` to parse CLI execution flags (`--panel`, `--window`, `--settings`, `--daemon`) and boot the PySide6 application lifecycle.
- Uses existing `SearchService`, `IndexService`, `FileSystemWatcher`, `PowerScheduler`, and `desktop actions` implemented in Phase 3.
- Utilizes `PySide6` (Qt 6) widgets, `pynput` for global hotkey hooks, and `PyMuPDF` / `Pillow` for preview image rendering.
- Adds test suites under `tests/test_gui_*.py` using `pytest-qt` / `QTest` abstractions with offscreen headless test execution (`QT_QPA_PLATFORM=offscreen`).
