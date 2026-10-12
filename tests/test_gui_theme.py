"""Unit tests for GUI visual design tokens, fonts, and stylesheets."""

from PySide6.QtWidgets import QApplication, QWidget
from semsearch.gui.theme import (
    ThemeColors,
    ThemeLayout,
    apply_theme,
    build_qss,
    get_mono_font,
    get_ui_font,
)


def test_theme_colors_constants():
    """Verify ThemeColors token values."""
    assert ThemeColors.BG_PANEL.startswith("rgba")
    assert ThemeColors.BRAND_START == "#6366F1"
    assert ThemeColors.BRAND_END == "#8B5CF6"
    assert ThemeColors.LEAF == "#10B981"
    assert ThemeColors.AMBER == "#F59E0B"
    assert "250, 204, 21" in ThemeColors.HIGHLIGHT_YELLOW


def test_theme_layout_constants():
    """Verify ThemeLayout geometry dimensions."""
    assert ThemeLayout.PANEL_WIDTH == 748
    assert ThemeLayout.PANEL_HEIGHT_COLLAPSED == 56
    assert ThemeLayout.PANEL_HEIGHT_EXPANDED == 480
    assert ThemeLayout.PANEL_RADIUS == 18
    assert ThemeLayout.LIST_WIDTH == 330
    assert ThemeLayout.PREVIEW_WIDTH == 418
    assert ThemeLayout.WINDOW_WIDTH == 1040
    assert ThemeLayout.WINDOW_HEIGHT == 700


def test_typography_helpers():
    """Verify font helpers configure family and size."""
    ui_font = get_ui_font(14)
    assert ui_font.pointSize() == 14

    mono_font = get_mono_font(11)
    assert mono_font.pointSize() == 11


def test_build_qss_and_apply_theme(qapp: QApplication):
    """Verify stylesheet generation and application to widget."""
    qss = build_qss()
    assert "QLineEdit" in qss
    assert "QListView" in qss
    assert ThemeColors.BRAND_START in qss

    widget = QWidget()
    apply_theme(widget)
    assert widget.styleSheet() == qss
