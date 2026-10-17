# Design

## Context

SemSearch currently instantiates both `SearchPanel` (the floating quick search launcher) and `SearchWindow` (a 1040x700 `QMainWindow` with kind filter chips, card grids, and code lists) inside `SemSearchApp.__init__()`. In `src/semsearch/main.py`, running `semsearch` without CLI flags defaults to `action = "SHOW_WINDOW"` and invokes `app.handle_cli_action("SHOW_WINDOW")` immediately after `app.start()`, opening the standalone window on every launch.

See `proposal.md` for motivation and capability scope.

## Goals / Non-Goals

**Goals:**
- Completely remove `SearchWindow` (`src/semsearch/gui/window.py`) and all UI/IPC/CLI hooks pointing to it.
- Ensure launching `semsearch` without flags starts the application anchored in the system tray without opening `SearchPanel` (only running `OnboardingWizard` when `config.onboarded` is `False`).
- Keep `SearchPanel` strictly on-demand via the configured global shortcut (`Ctrl+Shift+Space` by default), the system tray icon/menu, or `semsearch --panel`.
- Purge all unused i18n keys across all 22 language catalogs and update the `SearchPanel` footer shortcut hint in every language.

**Non-Goals:**
- Changing `SearchPanel` search ranking, preview capabilities, or `OnboardingWizard` steps.
- Modifying background indexing, file watching, or Ollama embedding services.

## Decisions

### 1. Default CLI Startup Action (`DAEMON`)
- **Choice**: Change `main()` in `src/semsearch/main.py` so the default action when no flags are provided is `"DAEMON"`, and remove `--window` from `build_parser()`.
- **Rationale**: When `action == "DAEMON"`, `app.start()` shows the system tray icon, runs `OnboardingWizard.exec()` if `not self.config.onboarded`, starts background indexing, and starts `HotkeyListener(self.config.hotkey)` without calling `show_panel()`. Keeping `--daemon` as an accepted flag preserves backward compatibility with existing `~/.config/autostart/semsearch.desktop` entries (`Exec=semsearch --daemon`).
- **Alternatives considered**: Removing `--daemon` entirely—rejected because existing autostart `.desktop` files created by `set_autostart_enabled()` pass `--daemon`.

### 2. Single-View Application Shell (`SemSearchApp`)
- **Choice**: Remove `self.window`, `show_window()`, `WINDOW`/`SHOW_WINDOW` IPC handling, and `open_window_requested` signal wiring from `SemSearchApp` (`src/semsearch/gui/app.py`), `SystemTrayManager` (`src/semsearch/gui/tray.py`), and `SearchPanel` (`src/semsearch/gui/panel.py`).
- **Rationale**: Eliminates eager instantiation of a hidden `QMainWindow`, its `PreviewPane`, `CardGridView`, and `CodeListView` at startup, reducing baseline widget and memory footprint.

### 3. Localization (`i18n`) Catalog Cleanup
- **Choice**: Remove the 14 string keys used exclusively by `window.py`, `--window`, and `tray.window_action` from all 22 language modules (`src/semsearch/i18n/*.py`), and replace `"↵ Open    ⇧↵ Show in Files    ⌃C Copy    ⌃O Full Window    Esc Dismiss"` with `"↵ Open    ⇧↵ Show in Files    ⌃C Copy    Esc Dismiss"` across `panel.py` and all 22 catalogs.
- **Rationale**: `tests/test_i18n.py` enforces exact key parity and placeholder matching across all 22 translation dictionaries using `es.STRINGS` as reference. Removing dead keys and updating the footer key across every language module keeps the translation catalog minimal and consistent.

## Risks / Trade-offs

- **[Risk] Existing autostart `.desktop` files or scripts passing `--daemon`** → Mitigation: Retain `--daemon` in `build_parser()` as a no-op alias for the default tray-only startup behavior.
- **[Risk] Key mismatch across the 22 language catalogs in `src/semsearch/i18n/`** → Mitigation: Update all 22 language files atomically and verify with `pytest tests/test_i18n.py`.
