# Proposal

## Why

When the quick search panel (`SearchPanel`) opens over dark-mode desktop applications (such as browsers on GitHub Dark, IDEs, or SemSearch's own main window), it blends into the background and appears semi-transparent. Its current surface color (`rgba(22, 24, 29, 0.94)`) has virtually identical luminance to standard dark UI backgrounds (`#15181D` / `#16181D`), its 6% alpha translucency lets underlying bright text bleed through without compositor blur, and its subtle `1px solid rgba(255, 255, 255, 0.12)` border and muted `#6B7280` prompt camouflages with surrounding UI borders.

## What Changes

- Update `ThemeColors.BG_PANEL` in `src/semsearch/gui/theme.py` to an elevated, fully opaque dark slate surface (`rgba(36, 41, 54, 1.0)` / `#242936`) that contrasts clearly against standard `#15181D`/`#16181D` dark backgrounds and eliminates text bleed-through from windows behind the panel.
- Update `QFrame#panelContainer` styling in `src/semsearch/gui/panel.py` to use a prominent `1.5px solid` accent border (`ThemeColors.BRAND_START` `#6366F1`) instead of the camouflaged `1px solid rgba(255, 255, 255, 0.12)` border.
- Improve visual contrast of the search prompt inside `SearchPanel` (`search_icon`, `search_input` placeholder text, and `status_badge`) using `ThemeColors.TEXT_SECONDARY` (`#9CA3AF`) so the floating search bar is immediately recognizable when invoked.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `search-panel`: Update `Floating Borderless Geometry and Dynamic Expansion` to require an opaque elevated panel surface, accent border framing, and high-contrast search prompt elements that visually separate the floating bar from underlying dark-mode windows without background bleed-through.

## Impact

- `src/semsearch/gui/theme.py`: `ThemeColors.BG_PANEL` token value.
- `src/semsearch/gui/panel.py`: `QFrame#panelContainer`, `search_icon`, `search_input`, and `status_badge` stylesheets.
- `tests/test_gui_theme.py` & `tests/test_gui_panel.py`: Unit tests verifying the updated panel surface opacity, accent border, and prompt contrast styling.
