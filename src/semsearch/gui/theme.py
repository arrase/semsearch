"""Visual design system, color tokens, and QSS stylesheets for SemSearch GUI."""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

ICONS_DIR = Path(__file__).parent / "icons"
CHEVRON_ICON = ICONS_DIR / "chevron-down.svg"
CHECK_ICON = ICONS_DIR / "check.svg"


class ThemeColors:
    """Design tokens matching DigUp dark palette and accents."""

    BG_PANEL = "rgba(36, 41, 54, 1.0)"
    BG_CARD = "rgba(32, 35, 43, 0.85)"
    BG_CARD_HOVER = "rgba(45, 50, 62, 0.85)"
    BG_CARD_ACTIVE = "rgba(60, 66, 82, 0.90)"
    BORDER = "rgba(255, 255, 255, 0.08)"
    BORDER_LIGHT = "rgba(255, 255, 255, 0.16)"
    TEXT_PRIMARY = "#F3F4F6"
    TEXT_SECONDARY = "#9CA3AF"
    TEXT_MUTED = "#94A3B8"
    BRAND_START = "#6366F1"
    BRAND_END = "#8B5CF6"
    LEAF = "#10B981"
    AMBER = "#F59E0B"
    HIGHLIGHT_YELLOW = "rgba(250, 204, 21, 0.35)"
    HIGHLIGHT_BORDER = "rgba(250, 204, 21, 0.8)"


@dataclass(frozen=True)
class ThemePalette:
    """Complete semantic color palette for a GUI theme."""

    bg_window: str
    bg_panel: str
    bg_panel_footer: str
    bg_card: str
    bg_card_hover: str
    bg_card_active: str
    bg_input_focus: str
    bg_menu: str
    bg_editor: str
    bg_gutter: str
    bg_list: str
    bg_tab_pane: str
    bg_group: str
    bg_subtle: str
    border: str
    border_light: str
    text_primary: str
    text_secondary: str
    text_muted: str
    brand_start: str
    brand_end: str
    leaf: str
    amber: str
    highlight_yellow: str
    highlight_border: str
    scrollbar_handle: str
    scrollbar_hover: str
    slider_groove: str
    selection_text: str


DARK_PALETTE = ThemePalette(
    bg_window="#16181D",
    bg_panel=ThemeColors.BG_PANEL,
    bg_panel_footer="rgba(15, 17, 21, 0.55)",
    bg_card=ThemeColors.BG_CARD,
    bg_card_hover=ThemeColors.BG_CARD_HOVER,
    bg_card_active=ThemeColors.BG_CARD_ACTIVE,
    bg_input_focus="rgba(36, 40, 50, 0.95)",
    bg_menu="#1E2128",
    bg_editor="#121418",
    bg_gutter="#1A1D24",
    bg_list="rgba(18, 20, 24, 0.55)",
    bg_tab_pane="rgba(22, 24, 29, 0.7)",
    bg_group="rgba(28, 31, 38, 0.65)",
    bg_subtle="rgba(255, 255, 255, 0.06)",
    border=ThemeColors.BORDER,
    border_light=ThemeColors.BORDER_LIGHT,
    text_primary=ThemeColors.TEXT_PRIMARY,
    text_secondary=ThemeColors.TEXT_SECONDARY,
    text_muted=ThemeColors.TEXT_MUTED,
    brand_start=ThemeColors.BRAND_START,
    brand_end=ThemeColors.BRAND_END,
    leaf=ThemeColors.LEAF,
    amber=ThemeColors.AMBER,
    highlight_yellow=ThemeColors.HIGHLIGHT_YELLOW,
    highlight_border=ThemeColors.HIGHLIGHT_BORDER,
    scrollbar_handle="rgba(255, 255, 255, 0.18)",
    scrollbar_hover="rgba(255, 255, 255, 0.35)",
    slider_groove="rgba(255, 255, 255, 0.14)",
    selection_text="#FFFFFF",
)

LIGHT_PALETTE = ThemePalette(
    bg_window="#F3F4F6",
    bg_panel="rgba(255, 255, 255, 0.98)",
    bg_panel_footer="rgba(241, 245, 249, 0.95)",
    bg_card="#FFFFFF",
    bg_card_hover="#F1F5F9",
    bg_card_active="#E0E7FF",
    bg_input_focus="#FFFFFF",
    bg_menu="#FFFFFF",
    bg_editor="#F8FAFC",
    bg_gutter="#EDF2F7",
    bg_list="#FFFFFF",
    bg_tab_pane="#FFFFFF",
    bg_group="rgba(255, 255, 255, 0.85)",
    bg_subtle="rgba(15, 23, 42, 0.06)",
    border="rgba(15, 23, 42, 0.12)",
    border_light="rgba(15, 23, 42, 0.22)",
    text_primary="#0F172A",
    text_secondary="#475569",
    text_muted="#64748B",
    brand_start="#4F46E5",
    brand_end="#7C3AED",
    leaf="#059669",
    amber="#D97706",
    highlight_yellow="rgba(250, 204, 21, 0.45)",
    highlight_border="rgba(217, 119, 6, 0.85)",
    scrollbar_handle="rgba(15, 23, 42, 0.22)",
    scrollbar_hover="rgba(15, 23, 42, 0.40)",
    slider_groove="rgba(15, 23, 42, 0.16)",
    selection_text="#0F172A",
)

_active_theme = "dark"


def resolve_theme(theme: str = "dark") -> str:
    """Resolve theme preference ('dark', 'light', 'system') into 'dark' or 'light'."""
    if theme == "light":
        return "light"
    if theme == "system":
        if QGuiApplication.instance() is not None:
            scheme = QGuiApplication.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Light:
                return "light"
        return "dark"
    return "dark"


def get_active_theme() -> str:
    """Return the currently active configured theme name."""
    return _active_theme


def set_active_theme(theme: str = "dark") -> None:
    """Set the active configured theme name."""
    global _active_theme
    _active_theme = theme


def get_palette(theme: str | None = None) -> ThemePalette:
    """Return the ThemePalette corresponding to the given or active theme."""
    resolved = resolve_theme(_active_theme if theme is None else theme)
    return LIGHT_PALETTE if resolved == "light" else DARK_PALETTE


class ThemeLayout:
    """Dimensions and layout constants for UI components."""

    PANEL_WIDTH = 748
    PANEL_HEIGHT_COLLAPSED = 56
    PANEL_HEIGHT_EXPANDED = 480
    PANEL_RADIUS = 18

    LIST_WIDTH = 328
    PREVIEW_WIDTH = 416


FONT_FAMILY_UI = "Inter, Noto Sans, Ubuntu, sans-serif"
FONT_FAMILY_MONO = "JetBrains Mono, Fira Code, monospace"


def get_ui_font(size: int = 13, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Create UI typography font."""
    font = QFont()
    font.setFamilies(["Inter", "Noto Sans", "Ubuntu", "sans-serif"])
    font.setPointSize(size)
    font.setWeight(weight)
    return font


def get_mono_font(size: int = 12, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Create monospace typography font for code snippet rendering."""
    font = QFont()
    font.setFamilies(["JetBrains Mono", "Fira Code"])
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSize(size)
    font.setWeight(weight)
    return font


def build_qss(theme: str | None = None) -> str:
    """Build QSS stylesheet for the requested or active theme."""
    p = get_palette(theme)
    return f"""
    QWidget {{
        color: {p.text_primary};
        selection-background-color: {p.brand_start};
        selection-color: #FFFFFF;
    }}

    QMainWindow, QDialog, QWizard, QWizardPage {{
        background-color: {p.bg_window};
        color: {p.text_primary};
    }}

    QLabel, QCheckBox, QRadioButton {{
        background: transparent;
    }}

    QScrollArea {{
        background: transparent;
        border: none;
    }}

    QMenu {{
        background-color: {p.bg_menu};
        color: {p.text_primary};
        border: 1px solid {p.border_light};
        border-radius: 8px;
        padding: 4px;
    }}

    QMenu::item {{
        padding: 6px 20px;
        border-radius: 5px;
    }}

    QMenu::item:selected {{
        background-color: {p.brand_start};
        color: #FFFFFF;
    }}

    QMenu::item:disabled {{
        color: {p.text_muted};
        background-color: transparent;
    }}

    QMenu::separator {{
        height: 1px;
        background: {p.border};
        margin: 4px 8px;
    }}

    QToolTip {{
        background-color: {p.bg_menu};
        color: {p.text_primary};
        border: 1px solid {p.border_light};
        border-radius: 6px;
        padding: 4px 8px;
    }}

    QGroupBox {{
        background-color: {p.bg_group};
        border: 1px solid {p.border};
        border-radius: 10px;
        margin-top: 10px;
        padding-top: 10px;
        font-weight: 600;
        color: {p.text_primary};
    }}

    QGroupBox::title {{
        subcontrol-origin: margin;
        padding: 0 6px;
        color: {p.text_secondary};
    }}

    QLineEdit {{
        background-color: {p.bg_card};
        color: {p.text_primary};
        border: 1px solid {p.border};
        border-radius: 10px;
        padding: 6px 12px;
    }}

    QLineEdit:focus {{
        border: 1px solid {p.brand_start};
        background-color: {p.bg_input_focus};
    }}

    QAbstractSpinBox, QTimeEdit, QSpinBox {{
        background-color: {p.bg_card};
        color: {p.text_primary};
        border: 1px solid {p.border_light};
        border-radius: 6px;
        padding: 4px 8px;
        min-height: 24px;
        min-width: 72px;
    }}

    QAbstractSpinBox QLineEdit {{
        padding: 0px;
        border: none;
        background: transparent;
    }}

    QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
        width: 0px;
        border: none;
    }}

    QPlainTextEdit, QTextEdit {{
        background-color: {p.bg_editor};
        color: {p.text_primary};
        border: 1px solid {p.border};
        border-radius: 8px;
        padding: 6px;
    }}

    QListView, QListWidget, QTableView, QTableWidget {{
        background-color: {p.bg_list};
        border: 1px solid {p.border};
        border-radius: 8px;
        outline: none;
    }}

    QListWidget#panelResultList {{
        background-color: transparent;
        border: none;
        border-radius: 0px;
    }}

    QListView::item, QListWidget::item {{
        padding: 6px 10px;
        border-radius: 6px;
        color: {p.text_primary};
        border: none;
    }}

    QListWidget#panelResultList::item {{
        padding: 0px;
        margin: 1px 6px;
        border-radius: 8px;
    }}

    QListView::item:hover, QListWidget::item:hover {{
        background-color: {p.bg_card_hover};
    }}

    QListView::item:selected, QListWidget::item:selected {{
        background-color: {p.bg_card_active};
        color: {p.selection_text};
    }}

    QSplitter::handle {{
        background-color: {p.border};
    }}

    QSplitter::handle:horizontal {{
        width: 1px;
    }}

    QSplitter::handle:vertical {{
        height: 1px;
    }}

    QSlider::groove:horizontal {{
        height: 4px;
        background: {p.slider_groove};
        border-radius: 2px;
    }}

    QSlider::sub-page:horizontal {{
        background: {p.brand_start};
        border-radius: 2px;
    }}

    QSlider::handle:horizontal {{
        background: #FFFFFF;
        border: 2px solid {p.brand_start};
        width: 12px;
        height: 12px;
        margin: -5px 0;
        border-radius: 7px;
    }}

    QScrollBar:vertical {{
        border: none;
        background: transparent;
        width: 6px;
        margin: 0;
    }}

    QScrollBar::handle:vertical {{
        background: {p.scrollbar_handle};
        min-height: 24px;
        border-radius: 3px;
    }}

    QScrollBar::handle:vertical:hover {{
        background: {p.scrollbar_hover};
    }}

    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: none;
        height: 0px;
    }}

    QScrollBar:horizontal {{
        border: none;
        background: transparent;
        height: 6px;
        margin: 0;
    }}

    QScrollBar::handle:horizontal {{
        background: {p.scrollbar_handle};
        min-width: 24px;
        border-radius: 3px;
    }}

    QScrollBar::handle:horizontal:hover {{
        background: {p.scrollbar_hover};
    }}

    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: none;
        width: 0px;
    }}

    QPushButton {{
        background-color: {p.bg_card};
        color: {p.text_primary};
        border: 1px solid {p.border};
        border-radius: 8px;
        padding: 6px 14px;
        font-weight: 500;
    }}

    QPushButton:hover {{
        background-color: {p.bg_card_hover};
        border-color: {p.border_light};
    }}

    QPushButton:pressed {{
        background-color: {p.bg_card_active};
    }}

    QPushButton:checked {{
        background-color: {p.brand_start};
        border-color: {p.brand_end};
        color: #FFFFFF;
    }}

    QTabWidget::pane {{
        border: 1px solid {p.border};
        border-radius: 10px;
        background-color: {p.bg_tab_pane};
        padding: 12px;
    }}

    QTabBar::tab {{
        background: transparent;
        color: {p.text_secondary};
        padding: 8px 16px;
        border-bottom: 2px solid transparent;
        font-weight: 500;
    }}

    QTabBar::tab:selected {{
        color: {p.text_primary};
        border-bottom: 2px solid {p.brand_start};
    }}

    QTabBar::tab:hover:!selected {{
        color: {p.text_primary};
    }}

    QComboBox {{
        background-color: {p.bg_card};
        color: {p.text_primary};
        border: 1px solid {p.border};
        border-radius: 8px;
        padding: 6px 12px;
    }}

    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}

    QComboBox::down-arrow {{
        image: url("{CHEVRON_ICON}");
        width: 12px;
        height: 12px;
    }}

    QComboBox QAbstractItemView {{
        background-color: {p.bg_menu};
        color: {p.text_primary};
        border: 1px solid {p.border_light};
        selection-background-color: {p.brand_start};
        selection-color: #FFFFFF;
    }}

    QCheckBox {{
        color: {p.text_primary};
        spacing: 8px;
    }}

    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border-radius: 5px;
        border: 1px solid {p.border_light};
        background: {p.bg_card};
    }}

    QCheckBox::indicator:checked {{
        background: {p.brand_start};
        border-color: {p.brand_start};
        image: url("{CHECK_ICON}");
    }}
    """


def apply_theme(target: QWidget | QApplication, theme: str | None = None) -> None:
    """Apply QSS theme to a widget, window, or QApplication."""
    global _active_theme
    if theme is not None:
        _active_theme = theme
    target.setStyleSheet(build_qss(theme))
