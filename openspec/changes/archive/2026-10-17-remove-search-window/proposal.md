# Proposal

## Why

Maintaining two parallel search interfaces ("Quick Search" floating panel and "Search Window" standalone desktop window) adds unnecessary UI complexity, duplicated search/preview wiring, and memory overhead, while launching a window automatically on startup interrupts the user's desktop workflow. Consolidating exclusively around the floating Quick Search panel and anchoring the application silently in the system tray on startup delivers a cleaner, faster, keyboard-driven desktop experience.

## What Changes

- **BREAKING**: Remove the standalone "Search Window" (`SearchWindow`, `CardGridView`, `CodeListView`, `ResultCard`) and delete `src/semsearch/gui/window.py` along with `tests/test_gui_window.py`.
- **BREAKING**: Remove the `--window` CLI flag and `SHOW_WINDOW` IPC command from `src/semsearch/main.py` and `src/semsearch/gui/app.py`.
- Change default startup behavior when launching `semsearch` without arguments: the application docks silently to the system tray and starts background indexing and the global hotkey listener (`Ctrl+Shift+Space` by default) without opening the Quick Search panel, unless `config.onboarded` is `False` (in which case the `OnboardingWizard` runs first).
- Remove the "Search Window" action and `open_window_requested` signal from `SystemTrayManager` (`src/semsearch/gui/tray.py`).
- Remove the `Ctrl+O` shortcut and `open_window_requested` signal from `SearchPanel` (`src/semsearch/gui/panel.py`), and update the panel footer hint to `"↵ Open    ⇧↵ Show in Files    ⌃C Copy    Esc Dismiss"`.
- Remove unused window layout constants (`WINDOW_WIDTH`, `WINDOW_HEIGHT`, `WINDOW_MIN_WIDTH`, `WINDOW_MIN_HEIGHT`) and `#codeResultList` QSS rules from `src/semsearch/gui/theme.py`.
- Remove all 14 obsolete `SearchWindow` / `--window` translation keys and update the panel footer hint key across all 22 language catalogs in `src/semsearch/i18n/*.py`.
- Update existing GUI, CLI, E2E, and i18n test suites and `README.md` to reflect the single-panel tray-first architecture.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `search-window`: Remove all requirements for the standalone desktop search window, card grid view, code list view, and drag-and-drop card export.
- `desktop-gui`: Update single-instance CLI/IPC invocation so default startup runs anchored in the system tray without opening any search UI (except onboarding when not yet onboarded), and remove Search Window actions from the system tray and app shell.
- `search-panel`: Remove the `Ctrl+O` full window transition shortcut and update the shortcut footer hint.
- `e2e-validation`: Remove the `--window` CLI flag dispatch scenario from CLI activation and IPC requirements.

## Impact

- **Code**: `src/semsearch/gui/window.py` (deleted), `src/semsearch/main.py`, `src/semsearch/gui/app.py`, `src/semsearch/gui/tray.py`, `src/semsearch/gui/panel.py`, `src/semsearch/gui/theme.py`.
- **Localization**: All 22 translation modules in `src/semsearch/i18n/` (`ar.py`, `be.py`, `bg.py`, `de.py`, `es.py`, `fa.py`, `fr.py`, `he.py`, `id.py`, `it.py`, `kk.py`, `ms.py`, `nl.py`, `pl.py`, `pt.py`, `ru.py`, `sr.py`, `th.py`, `tl.py`, `tr.py`, `uk.py`, `vi.py`).
- **Tests & Docs**: `tests/test_gui_window.py` (deleted), `tests/test_gui_app.py`, `tests/test_gui_tray.py`, `tests/test_gui_panel.py`, `tests/test_e2e_cli.py`, `tests/test_e2e_integration.py`, `tests/test_e2e_invariants.py`, `tests/test_i18n.py`, `README.md`.
