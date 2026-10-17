# Design

## Context

See `proposal.md` for problem motivation. In `src/semsearch/gui/panel.py`, `SearchPanel` is a frameless `QWidget` with `Qt.WidgetAttribute.WA_TranslucentBackground` that enables rounded corners (`ThemeLayout.PANEL_RADIUS = 18`). Inside it, `QFrame#panelContainer` paints the visible surface using `ThemeColors.BG_PANEL`. Because `WA_TranslucentBackground` does not trigger desktop compositor blur on standard X11/Wayland tool windows, any alpha `< 1.0` in `BG_PANEL` directly composites underlying window text into the search bar. Furthermore, `rgb(22, 24, 29)` matches standard dark-mode surfaces (`#15181D` / `#16181D`), leaving zero luminance separation.

## Goals / Non-Goals

**Goals:**
- Provide clear visual separation between `SearchPanel` and dark-mode desktop windows behind it.
- Eliminate background text bleed-through inside the panel container while preserving `WA_TranslucentBackground` for the 18px rounded window corners.
- Preserve existing window dimensions (`748x56` collapsed, `748x480` expanded) and layout margins (`0, 0, 0, 0`).

**Non-Goals:**
- Adding outer `QGraphicsDropShadowEffect` margins that would alter `PANEL_WIDTH` or `PANEL_HEIGHT_COLLAPSED`.
- Changing the main standalone `SearchWindow` color scheme.

## Decisions

1. **Elevated Opaque Surface Token (`ThemeColors.BG_PANEL`)**:
   - **Choice**: Change `ThemeColors.BG_PANEL` from `"rgba(22, 24, 29, 0.94)"` to `"rgba(36, 41, 54, 1.0)"` (`#242936`).
   - **Rationale**: Keeping the `rgba(...)` string format preserves backward compatibility with theme token expectations while setting alpha to `1.0` (100% opaque) to prevent underlying high-contrast text from bleeding through. The slate-indigo tint `rgb(36, 41, 54)` provides a distinct luminance step above `#15181D`/`#16181D` dark backgrounds.
   - **Alternatives considered**: `QGraphicsDropShadowEffect` (Option B), rejected because Qt clips drop shadows at widget boundaries unless outer layout margins are added, which would either shrink internal space or change window geometry constants.

2. **Brand Accent Border on `QFrame#panelContainer`**:
   - **Choice**: Style `QFrame#panelContainer` with `border: 1.5px solid #6366F1` (`ThemeColors.BRAND_START`).
   - **Rationale**: Replaces the subtle `1px solid rgba(255, 255, 255, 0.12)` border (`#323338`) that blends into dark webpage/IDE card borders with a crisp brand-indigo perimeter ring typical of desktop command palettes.

3. **Higher Contrast Search Header Elements**:
   - **Choice**: Use `ThemeColors.TEXT_SECONDARY` (`#9CA3AF`) for `search_icon`, `status_badge`, and `QLineEdit` placeholder text color inside `SearchPanel`.
   - **Rationale**: Ensures the search prompt is immediately legible against the `#242936` elevated surface.

## Risks / Trade-offs

- **[Risk] Rounded corner anti-aliasing on non-composited X11 sessions** → Mitigation: `WA_TranslucentBackground` remains enabled on the top-level `SearchPanel` widget so the area outside the 18px border-radius stays transparent while the interior of `QFrame#panelContainer` is 100% opaque.
