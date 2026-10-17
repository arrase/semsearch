# Tasks

## 1. Remove Standalone Search Window and Theme Relics

- [x] 1.1 Delete `src/semsearch/gui/window.py` and `tests/test_gui_window.py`, and verify neither file exists on disk
- [x] 1.2 Remove `WINDOW_WIDTH`, `WINDOW_HEIGHT`, `WINDOW_MIN_WIDTH`, `WINDOW_MIN_HEIGHT` from `ThemeLayout` and `#codeResultList` selectors from `build_qss()` in `src/semsearch/gui/theme.py`, and verify `.venv/bin/pytest tests/test_gui_preview.py` passes

## 2. Update Search Panel, System Tray, Application Shell, and CLI Startup

- [x] 2.1 Remove `open_window_requested` signal, `Ctrl+O` handler in `eventFilter()`, and `⌃O Full Window` from `footer_hint` in `src/semsearch/gui/panel.py`, update `tests/test_gui_panel.py`, and verify `.venv/bin/pytest tests/test_gui_panel.py` passes
- [x] 2.2 Remove `open_window_requested` signal and `window_action` ("Search Window") from `src/semsearch/gui/tray.py`, update `tests/test_gui_tray.py`, and verify `.venv/bin/pytest tests/test_gui_tray.py` passes
- [x] 2.3 Remove `SearchWindow` import, `self.window`, `show_window()`, `open_window_requested` connections, and `WINDOW`/`SHOW_WINDOW` IPC routing from `src/semsearch/gui/app.py`, and update `src/semsearch/main.py` to remove `--window` and default startup action to `"DAEMON"` (system tray only unless onboarding is required)
- [x] 2.4 Update `tests/test_gui_app.py`, `tests/test_e2e_cli.py`, `tests/test_e2e_integration.py`, `tests/test_e2e_invariants.py`, and `README.md` to reflect tray-only startup and removal of `--window`/`SearchWindow`, and verify `.venv/bin/pytest tests/test_gui_app.py tests/test_e2e_cli.py tests/test_e2e_integration.py tests/test_e2e_invariants.py` passes

## 3. Clean Up Localization Catalogs (`i18n`)

- [x] 3.1 Remove the 14 obsolete `SearchWindow` / `--window` translation keys and update the panel footer shortcut key (`"↵ Open    ⇧↵ Show in Files    ⌃C Copy    Esc Dismiss"`) across all 22 language modules in `src/semsearch/i18n/*.py`
- [x] 3.2 Update `tests/test_i18n.py` to remove `SearchWindow` and obsolete key assertions, and verify `.venv/bin/pytest tests/test_i18n.py` passes

## 4. Full Suite Verification

- [x] 4.1 Run the full test suite with `.venv/bin/pytest` and verify all tests pass with zero remaining references to `SearchWindow` or `gui.window`
