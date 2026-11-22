"""Unit tests for GUI visual design tokens, dark/light/system themes, WCAG contrast, fonts, RTL QSS, and helpers."""

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

from semsearch.gui.preview import (
    AudioPreviewWidget,
    CodePreviewWidget,
    EmptyPreviewWidget,
    ImagePreviewWidget,
    MetadataCard,
    PdfPreviewWidget,
    VideoPreviewWidget,
)
from semsearch.gui.theme import (
    DARK_PALETTE,
    LIGHT_PALETTE,
    ThemeColors,
    ThemeLayout,
    apply_theme,
    build_qss,
    get_active_theme,
    get_mono_font,
    get_palette,
    get_ui_font,
    resolve_theme,
)
from semsearch.gui.widgets import FolderListEditor, qt_escape_amp


def _relative_luminance(hex_color: str) -> float:
    cleaned = hex_color.lstrip("#")
    channels = [int(cleaned[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    l1 = _relative_luminance(fg_hex)
    l2 = _relative_luminance(bg_hex)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def test_theme_colors_constants_and_wcag_contrast() -> None:
    """Verify ThemeColors tokens and WCAG AA (>= 5.2:1) contrast for TEXT_MUTED."""
    assert ThemeColors.BG_PANEL == "rgba(36, 41, 54, 1.0)"
    assert ThemeColors.BG_PANEL.endswith(", 1.0)")
    assert ThemeColors.TEXT_MUTED == "#94A3B8"
    assert ThemeColors.BRAND_START == "#6366F1"
    assert ThemeColors.BRAND_END == "#8B5CF6"
    assert ThemeColors.LEAF == "#10B981"
    assert ThemeColors.AMBER == "#F59E0B"
    assert "250, 204, 21" in ThemeColors.HIGHLIGHT_YELLOW

    assert _contrast_ratio(ThemeColors.TEXT_MUTED, "#242936") >= 5.2
    assert _contrast_ratio(ThemeColors.TEXT_MUTED, "#1E222B") >= 5.2


def test_theme_layout_constants() -> None:
    """Verify ThemeLayout geometry dimensions sum exactly to PANEL_WIDTH including borders."""
    assert ThemeLayout.PANEL_WIDTH == 748
    assert ThemeLayout.PANEL_HEIGHT_COLLAPSED == 56
    assert ThemeLayout.PANEL_HEIGHT_EXPANDED == 480
    assert ThemeLayout.PANEL_RADIUS == 18
    assert ThemeLayout.LIST_WIDTH == 328
    assert ThemeLayout.PREVIEW_WIDTH == 416
    assert ThemeLayout.LIST_WIDTH + ThemeLayout.PREVIEW_WIDTH + 4 == ThemeLayout.PANEL_WIDTH
    assert not hasattr(ThemeLayout, "WINDOW_WIDTH")
    assert not hasattr(ThemeLayout, "WINDOW_HEIGHT")


def test_typography_helpers() -> None:
    """Verify font helpers configure family and size."""
    ui_font = get_ui_font(14)
    assert ui_font.pointSize() == 14

    mono_font = get_mono_font(11)
    assert mono_font.pointSize() == 11


def test_build_qss_rtl_safe_and_menu_styles(qapp: QApplication) -> None:
    """Verify QSS drops hardcoded LTR QGroupBox offsets, styles QMenu, and applies to widgets and QApplication."""
    qss = build_qss()
    assert "QLineEdit" in qss
    assert "QListView" in qss
    assert "QMenu {" in qss
    assert "QMenu::item {" in qss
    assert "QMenu::item:disabled {" in qss
    assert "subcontrol-origin: margin;" in qss
    assert "subcontrol-position: top left;" not in qss
    assert "left: 12px;" not in qss
    assert ThemeColors.BRAND_START in qss

    widget = QWidget()
    apply_theme(widget)
    assert widget.styleSheet() == qss

    try:
        apply_theme(qapp)
        assert qapp.styleSheet() == qss
    finally:
        qapp.setStyleSheet("")


def test_qt_escape_amp_and_folder_list_editor(qapp: QApplication) -> None:
    """Verify qt_escape_amp escaping and FolderListEditor deduplication."""
    del qapp
    assert qt_escape_amp("Storage & Model") == "Storage && Model"
    assert qt_escape_amp("Storage && Model") == "Storage && Model"
    assert qt_escape_amp("Power & Schedule") == "Power && Schedule"

    editor = FolderListEditor("Add", "Remove", "Pick")
    changes: list[int] = []
    editor.folders_changed.connect(lambda: changes.append(1))

    assert editor.add_folder("/tmp/docs") is True
    assert editor.add_folder("/tmp/docs") is False
    assert editor.add_folder("   ") is False
    assert editor.get_folders() == ["/tmp/docs"]

    editor.set_folders(["/tmp/a", "/tmp/b", "/tmp/a"])
    assert editor.get_folders() == ["/tmp/a", "/tmp/b"]
    assert len(changes) == 2

    editor.retranslate_ui("Añadir", "Eliminar", "Seleccionar")
    assert editor.add_btn.text() == "Añadir"
    assert editor.remove_btn.text() == "Eliminar"


def test_theme_resolution_dark_light_and_system(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify resolve_theme, get_palette, build_qss, and apply_theme across dark, light, and system modes."""
    del qapp
    assert resolve_theme("dark") == "dark"
    assert resolve_theme("light") == "light"
    assert get_palette("dark") == DARK_PALETTE
    assert get_palette("light") == LIGHT_PALETTE

    light_hints = MagicMock()
    light_hints.colorScheme.return_value = Qt.ColorScheme.Light
    monkeypatch.setattr(QGuiApplication, "styleHints", staticmethod(lambda: light_hints))
    assert resolve_theme("system") == "light"
    assert get_palette("system") == LIGHT_PALETTE

    dark_hints = MagicMock()
    dark_hints.colorScheme.return_value = Qt.ColorScheme.Dark
    monkeypatch.setattr(QGuiApplication, "styleHints", staticmethod(lambda: dark_hints))
    assert resolve_theme("system") == "dark"
    assert get_palette("system") == DARK_PALETTE

    dark_qss = build_qss("dark")
    light_qss = build_qss("light")
    assert dark_qss != light_qss
    assert DARK_PALETTE.bg_window in dark_qss
    assert LIGHT_PALETTE.bg_window in light_qss
    assert f"color: {LIGHT_PALETTE.selection_text};" in light_qss

    widget = QWidget()
    try:
        apply_theme(widget, "light")
        assert get_active_theme() == "light"
        assert get_palette() == LIGHT_PALETTE
        assert widget.styleSheet() == light_qss
    finally:
        apply_theme(widget, "dark")
    assert get_active_theme() == "dark"


def test_preview_widgets_refresh_theme_updates_palette(qapp: QApplication) -> None:
    """Verify all preview widgets refresh inline styles when switching between dark and light themes."""
    del qapp
    host = QWidget()
    card = MetadataCard()
    empty = EmptyPreviewWidget()
    img = ImagePreviewWidget()
    pdf = PdfPreviewWidget()
    audio = AudioPreviewWidget()
    video = VideoPreviewWidget()
    code = CodePreviewWidget()

    try:
        apply_theme(host, "light")
        for view in (card, empty, img, pdf, audio, video, code):
            view.refresh_theme()

        assert LIGHT_PALETTE.bg_card in card.styleSheet()
        assert LIGHT_PALETTE.text_primary in card.name_label.styleSheet()
        assert LIGHT_PALETTE.text_muted in empty.text_label.styleSheet()
        assert LIGHT_PALETTE.bg_subtle in img.image_label.styleSheet()
        assert LIGHT_PALETTE.text_muted in img.dimensions_label.styleSheet()
        assert LIGHT_PALETTE.bg_subtle in pdf.page_label.styleSheet()
        assert LIGHT_PALETTE.text_secondary in pdf.page_info.styleSheet()
        assert LIGHT_PALETTE.text_secondary in audio.time_label.styleSheet()
        assert LIGHT_PALETTE.bg_subtle in video.poster_label.styleSheet()
        assert LIGHT_PALETTE.bg_card in code.meta_card.styleSheet()
    finally:
        apply_theme(host, "dark")
        img.shutdown()
        audio.shutdown()
        video.shutdown()
        code.shutdown()
        pdf.clear()
