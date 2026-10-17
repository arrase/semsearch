# Tasks

## 1. Preview Action Signal Propagation

- [x] 1.1 Add `action_triggered = Signal()` to `MetadataCard` and `PreviewPane` in `src/semsearch/gui/preview.py`, emitting it when `Open` or `Show in Files` buttons are clicked, and verify with unit tests in `tests/test_gui_preview.py`.

## 2. Search Panel Dismissal on Focus Loss and Preview Actions

- [x] 2.1 Implement `event` in `SearchPanel` (`src/semsearch/gui/panel.py`) to call `hide_panel()` on `QEvent.Type.WindowDeactivate` when visible, connect `self.preview_pane.action_triggered` to `self.hide_panel`, and update `SemSearchApp.toggle_panel()` in `src/semsearch/gui/app.py`.
- [x] 2.2 Add unit tests in `tests/test_gui_panel.py` verifying that `WindowDeactivate` dismisses the panel, internal focus changes to `result_list` or `preview_pane` keep the panel visible, and clicking preview `Open` / `Show in Files` buttons dismisses the panel; verify the full test suite passes via `.venv/bin/pytest`.
