"""Visual design system, color tokens, and QSS stylesheets for SemSearch GUI."""

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QWidget


class ThemeColors:
    """Design tokens matching DigUp dark palette and accents."""

    BG_PANEL = "rgba(22, 24, 29, 0.94)"
    BG_CARD = "rgba(32, 35, 43, 0.85)"
    BG_CARD_HOVER = "rgba(45, 50, 62, 0.85)"
    BG_CARD_ACTIVE = "rgba(60, 66, 82, 0.90)"
    BORDER = "rgba(255, 255, 255, 0.08)"
    BORDER_LIGHT = "rgba(255, 255, 255, 0.16)"
    TEXT_PRIMARY = "#F3F4F6"
    TEXT_SECONDARY = "#9CA3AF"
    TEXT_MUTED = "#6B7280"
    BRAND_START = "#6366F1"
    BRAND_END = "#8B5CF6"
    LEAF = "#10B981"
    AMBER = "#F59E0B"
    HIGHLIGHT_YELLOW = "rgba(250, 204, 21, 0.35)"
    HIGHLIGHT_BORDER = "rgba(250, 204, 21, 0.8)"


class ThemeLayout:
    """Dimensions and layout constants for UI components."""

    PANEL_WIDTH = 748
    PANEL_HEIGHT_COLLAPSED = 56
    PANEL_HEIGHT_EXPANDED = 480
    PANEL_RADIUS = 18

    LIST_WIDTH = 330
    PREVIEW_WIDTH = 418

    WINDOW_WIDTH = 1040
    WINDOW_HEIGHT = 700
    WINDOW_MIN_WIDTH = 800
    WINDOW_MIN_HEIGHT = 500


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
    font.setFamilies(["JetBrains Mono", "Fira Code", "monospace"])
    font.setPointSize(size)
    font.setWeight(weight)
    return font


def build_qss() -> str:
    """Build standardized dark mode QSS stylesheet for the application."""
    return f"""
    QWidget {{
        color: {ThemeColors.TEXT_PRIMARY};
        selection-background-color: {ThemeColors.BRAND_START};
        selection-color: #FFFFFF;
    }}

    QMainWindow, QDialog, QWizard, QWizardPage {{
        background-color: #16181D;
        color: {ThemeColors.TEXT_PRIMARY};
    }}

    QLabel, QCheckBox, QRadioButton {{
        background: transparent;
    }}

    QScrollArea {{
        background: transparent;
        border: none;
    }}

    QMenu {{
        background-color: #1E2128;
        color: {ThemeColors.TEXT_PRIMARY};
        border: 1px solid {ThemeColors.BORDER_LIGHT};
        border-radius: 8px;
        padding: 4px;
    }}

    QMenu::item {{
        padding: 6px 20px;
        border-radius: 5px;
    }}

    QMenu::item:selected {{
        background-color: {ThemeColors.BRAND_START};
        color: #FFFFFF;
    }}

    QMenu::separator {{
        height: 1px;
        background: {ThemeColors.BORDER};
        margin: 4px 8px;
    }}

    QToolTip {{
        background-color: #20232B;
        color: {ThemeColors.TEXT_PRIMARY};
        border: 1px solid {ThemeColors.BORDER_LIGHT};
        border-radius: 6px;
        padding: 4px 8px;
    }}

    QGroupBox {{
        background-color: rgba(28, 31, 38, 0.65);
        border: 1px solid {ThemeColors.BORDER};
        border-radius: 10px;
        margin-top: 10px;
        padding-top: 10px;
        font-weight: 600;
        color: {ThemeColors.TEXT_PRIMARY};
    }}

    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 12px;
        padding: 0 6px;
        color: {ThemeColors.TEXT_SECONDARY};
    }}

    QLineEdit {{
        background-color: {ThemeColors.BG_CARD};
        color: {ThemeColors.TEXT_PRIMARY};
        border: 1px solid {ThemeColors.BORDER};
        border-radius: 10px;
        padding: 6px 12px;
    }}

    QLineEdit:focus {{
        border: 1px solid {ThemeColors.BRAND_START};
        background-color: rgba(36, 40, 50, 0.95);
    }}

    QAbstractSpinBox, QTimeEdit, QSpinBox {{
        background-color: {ThemeColors.BG_CARD};
        color: {ThemeColors.TEXT_PRIMARY};
        border: 1px solid {ThemeColors.BORDER_LIGHT};
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

    QPlainTextEdit, QTextEdit {{
        background-color: #121418;
        color: {ThemeColors.TEXT_PRIMARY};
        border: 1px solid {ThemeColors.BORDER};
        border-radius: 8px;
        padding: 6px;
    }}

    QListView, QListWidget, QTableView, QTableWidget {{
        background-color: rgba(18, 20, 24, 0.55);
        border: 1px solid {ThemeColors.BORDER};
        border-radius: 8px;
        outline: none;
    }}

    QListWidget#panelResultList, QListWidget#codeResultList {{
        background-color: transparent;
        border: none;
        border-radius: 0px;
    }}

    QListView::item, QListWidget::item {{
        padding: 6px 10px;
        border-radius: 6px;
        color: {ThemeColors.TEXT_PRIMARY};
        border: none;
    }}

    QListWidget#panelResultList::item, QListWidget#codeResultList::item {{
        padding: 0px;
        margin: 1px 6px;
        border-radius: 8px;
    }}

    QListView::item:hover, QListWidget::item:hover {{
        background-color: {ThemeColors.BG_CARD_HOVER};
    }}

    QListView::item:selected, QListWidget::item:selected {{
        background-color: {ThemeColors.BG_CARD_ACTIVE};
        color: #FFFFFF;
    }}

    QSplitter::handle {{
        background-color: {ThemeColors.BORDER};
    }}

    QSplitter::handle:horizontal {{
        width: 1px;
    }}

    QSplitter::handle:vertical {{
        height: 1px;
    }}

    QSlider::groove:horizontal {{
        height: 4px;
        background: rgba(255, 255, 255, 0.14);
        border-radius: 2px;
    }}

    QSlider::sub-page:horizontal {{
        background: {ThemeColors.BRAND_START};
        border-radius: 2px;
    }}

    QSlider::handle:horizontal {{
        background: #FFFFFF;
        border: 2px solid {ThemeColors.BRAND_START};
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
        background: rgba(255, 255, 255, 0.18);
        min-height: 24px;
        border-radius: 3px;
    }}

    QScrollBar::handle:vertical:hover {{
        background: rgba(255, 255, 255, 0.35);
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
        background: rgba(255, 255, 255, 0.18);
        min-width: 24px;
        border-radius: 3px;
    }}

    QScrollBar::handle:horizontal:hover {{
        background: rgba(255, 255, 255, 0.35);
    }}

    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: none;
        width: 0px;
    }}

    QPushButton {{
        background-color: {ThemeColors.BG_CARD};
        color: {ThemeColors.TEXT_PRIMARY};
        border: 1px solid {ThemeColors.BORDER};
        border-radius: 8px;
        padding: 6px 14px;
        font-weight: 500;
    }}

    QPushButton:hover {{
        background-color: {ThemeColors.BG_CARD_HOVER};
        border-color: {ThemeColors.BORDER_LIGHT};
    }}

    QPushButton:pressed {{
        background-color: {ThemeColors.BG_CARD_ACTIVE};
    }}

    QPushButton:checked {{
        background-color: {ThemeColors.BRAND_START};
        border-color: {ThemeColors.BRAND_END};
        color: #FFFFFF;
    }}

    QTabWidget::pane {{
        border: 1px solid {ThemeColors.BORDER};
        border-radius: 10px;
        background-color: rgba(22, 24, 29, 0.7);
        padding: 12px;
    }}

    QTabBar::tab {{
        background: transparent;
        color: {ThemeColors.TEXT_SECONDARY};
        padding: 8px 16px;
        border-bottom: 2px solid transparent;
        font-weight: 500;
    }}

    QTabBar::tab:selected {{
        color: {ThemeColors.TEXT_PRIMARY};
        border-bottom: 2px solid {ThemeColors.BRAND_START};
    }}

    QTabBar::tab:hover:!selected {{
        color: {ThemeColors.TEXT_PRIMARY};
    }}

    QComboBox {{
        background-color: {ThemeColors.BG_CARD};
        color: {ThemeColors.TEXT_PRIMARY};
        border: 1px solid {ThemeColors.BORDER};
        border-radius: 8px;
        padding: 6px 12px;
    }}

    QComboBox QAbstractItemView {{
        background-color: #20232B;
        color: {ThemeColors.TEXT_PRIMARY};
        border: 1px solid {ThemeColors.BORDER_LIGHT};
        selection-background-color: {ThemeColors.BRAND_START};
    }}

    QCheckBox {{
        color: {ThemeColors.TEXT_PRIMARY};
        spacing: 8px;
    }}

    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border-radius: 5px;
        border: 1px solid {ThemeColors.BORDER_LIGHT};
        background: {ThemeColors.BG_CARD};
    }}

    QCheckBox::indicator:checked {{
        background: {ThemeColors.BRAND_START};
        border-color: {ThemeColors.BRAND_START};
    }}
    """



def apply_theme(widget: QWidget) -> None:
    """Apply standard QSS theme to a widget or window."""
    widget.setStyleSheet(build_qss())
