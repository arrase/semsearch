# Design

## Context

`SearchPanel` (`src/semsearch/gui/panel.py`) is a frameless top-level `QWidget` (`FramelessWindowHint | WindowStaysOnTopHint | Tool`) that acts as a floating quick launcher. Currently, it only hides on explicit keyboard actions (`Esc`, `Enter`, `Ctrl+O`), double-clicking a result, or pressing `Ctrl+Shift+Space` while active. Because it lacks a window deactivation handler, clicking outside the panel leaves it floating on top of other applications. See `proposal.md` for motivation.

## Goals / Non-Goals

**Goals:**
- Dismiss `SearchPanel` via `hide_panel()` as soon as the top-level `SearchPanel` window loses active status (`QEvent.Type.WindowDeactivate`).
- Preserve normal mouse/keyboard interaction with child widgets inside `SearchPanel` (`result_list`, `preview_pane`, weaker matches toggle).
- Dismiss `SearchPanel` when `Open` or `Show in Files` buttons inside `PreviewPane` (`MetadataCard`) are clicked, without affecting `SearchWindow` (which also embeds `PreviewPane`).

**Non-Goals:**
- Modifying `SearchWindow` focus behavior (the standalone window remains open when deactivated).
- Changing global hotkey registration or `HotkeyListener` internals.

## Decisions

1. **Use `QWidget.event` with `QEvent.Type.WindowDeactivate` on `SearchPanel` instead of `FocusOut` on `search_input`**
   - *Rationale*: `QEvent.Type.WindowDeactivate` is delivered to the top-level `SearchPanel` widget (`QWidget.event`) only when window activation leaves the panel altogether (e.g., clicking another application, desktop, or another window). Clicking child widgets inside `SearchPanel` (`result_list`, `preview_pane`) changes widget focus within the same active window and does not trigger `WindowDeactivate`.
   - *Alternative considered*: Listening to `QEvent.Type.FocusOut` on `self.search_input`. Rejected because clicking a result row, expanding weaker matches, or scrolling the preview transfers widget focus away from `search_input` and would prematurely close the panel.

2. **Emit `action_triggered` signal from `MetadataCard` and forward through `PreviewPane`**
   - *Rationale*: `PreviewPane` is shared between `SearchPanel` (which should close after opening/revealing a file) and `SearchWindow` (which should remain open). Having `MetadataCard` emit `action_triggered = Signal()` when `Open` or `Show in Files` is clicked, forwarding it via `PreviewPane.action_triggered`, and connecting it to `self.hide_panel` only in `SearchPanel` keeps `PreviewPane` decoupled from its parent container.

3. **Simplify `SemSearchApp.toggle_panel()`**
   - *Rationale*: Since `SearchPanel` automatically hides upon losing window activation, whenever `self.panel.isVisible()` is true, toggling the hotkey should call `self.panel.hide_panel()`.

## Risks / Trade-offs

- **[Risk] Headless/offscreen Qt tests where `isActiveWindow()` is false by default** → `WindowDeactivate` only fires when a window transitions from active to inactive; calling `hide_panel()` in `event` when `event.type() == QEvent.Type.WindowDeactivate and self.isVisible()` ensures unit tests that simply call `panel.show()` without activating then deactivating the window are unaffected, while explicit `QEvent(QEvent.Type.WindowDeactivate)` tests can deterministically verify dismissal.
