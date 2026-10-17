# Proposal

## Why

When the quick search panel (`SearchPanel`) is opened via `Ctrl+Shift+Space`, it stays on top of all windows (`WindowStaysOnTopHint`) and grabs focus. Currently, it only closes when the user explicitly presses `Ctrl+Shift+Space` or `Esc` (or activates a result with `Enter`/double-click), remaining open and obstructing the screen if another window or desktop element takes focus. As a spotlight-style floating launcher, the panel must automatically dismiss itself whenever the panel window loses active focus.

## What Changes

- Automatically dismiss `SearchPanel` via `hide_panel()` when the panel window is deactivated (`QEvent.Type.WindowDeactivate`), while allowing focus changes between child widgets inside the panel itself (such as clicking items in the result list or interacting with the preview pane).
- Dismiss `SearchPanel` via `hide_panel()` when the user triggers file actions (`Open` or `Show in Files`) from the preview pane's metadata card inside the quick search panel.
- Simplify `SemSearchApp.toggle_panel()` to hide the panel whenever it is visible, since an open panel is always the active window until deactivated.

## Capabilities

### New Capabilities

### Modified Capabilities
- `search-panel`: Add automatic dismissal when the quick search panel window loses active window focus (`WindowDeactivate`) or when preview pane file action buttons are activated.

## Impact

- `src/semsearch/gui/panel.py`: Handle `QEvent.Type.WindowDeactivate` in `SearchPanel.event` and connect preview action signals to `hide_panel()`.
- `src/semsearch/gui/preview.py`: Emit a signal from `MetadataCard` / `PreviewPane` when `Open` or `Show in Files` buttons are clicked.
- `src/semsearch/gui/app.py`: Update `SemSearchApp.toggle_panel()` visibility check.
- `tests/test_gui_panel.py`: Add unit tests verifying panel dismissal on window deactivation and preview action triggers without breaking internal child widget clicks.
