# Tasks

## 1. Theme & Search Panel Contrast Styling

- [x] 1.1 Update `ThemeColors.BG_PANEL` in `src/semsearch/gui/theme.py` to the elevated opaque slate color `"rgba(36, 41, 54, 1.0)"` and verify `tests/test_gui_theme.py` asserts full opacity (`1.0`) and passes via `.venv/bin/pytest tests/test_gui_theme.py`
- [x] 1.2 Update `QFrame#panelContainer`, `search_icon`, `search_input`, and `status_badge` styling in `src/semsearch/gui/panel.py` to use `1.5px solid {ThemeColors.BRAND_START}` for the panel border and `ThemeColors.TEXT_SECONDARY` for prompt/icon contrast, and verify unit tests in `tests/test_gui_panel.py` cover the container border and surface styling and pass via `.venv/bin/pytest tests/test_gui_panel.py`
