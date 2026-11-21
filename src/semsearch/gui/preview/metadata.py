"""Metadata display card, empty placeholder widget, and formatting helpers for preview pane."""

from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from semsearch.config import AppConfig
from semsearch.core.types import SearchHit
from semsearch.gui.theme import ThemeColors, get_ui_font
from semsearch.gui.widgets import ElidedLabel
from semsearch.i18n import tr
from semsearch.services.actions import execute_hit_action, open_file, reveal_in_file_manager

_BYTES_PER_KB = 1024


def format_file_size(size_bytes: int) -> str:
    """Format byte size into human readable string."""
    if size_bytes < _BYTES_PER_KB:
        return f"{size_bytes} B"
    if size_bytes < _BYTES_PER_KB * _BYTES_PER_KB:
        return f"{size_bytes / _BYTES_PER_KB:.1f} KB"
    if size_bytes < _BYTES_PER_KB * _BYTES_PER_KB * _BYTES_PER_KB:
        return f"{size_bytes / (_BYTES_PER_KB * _BYTES_PER_KB):.1f} MB"
    return f"{size_bytes / (_BYTES_PER_KB * _BYTES_PER_KB * _BYTES_PER_KB):.1f} GB"


def format_tilde_path(path_str: str) -> str:
    """Convert home directory prefix into tilde notation."""
    home = str(Path.home())
    if path_str.startswith(home):
        return "~" + path_str[len(home) :]
    return path_str


class MetadataCard(QFrame):
    """Compact metadata display showing file details, timestamp, match reason, and file actions."""

    action_triggered = Signal()

    def __init__(self, config: AppConfig | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._path: str = ""
        self._reason: str = ""
        self._hit: SearchHit | None = None
        self._config: AppConfig | None = config
        self.setObjectName("metadataCard")
        self.setStyleSheet(
            f"QFrame#metadataCard {{ background-color: {ThemeColors.BG_CARD}; "
            f"border: 1px solid {ThemeColors.BORDER}; border-radius: 8px; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        self.name_label = ElidedLabel()
        self.name_label.setFont(get_ui_font(12, QFont.Weight.DemiBold))
        self.name_label.setStyleSheet(f"color: {ThemeColors.TEXT_PRIMARY}; border: none; background: transparent;")

        self.reason_badge = QLabel()
        self.reason_badge.setFont(get_ui_font(10, QFont.Weight.Medium))
        self.reason_badge.setStyleSheet(
            f"background-color: rgba(99, 102, 241, 0.2); color: {ThemeColors.BRAND_START};"
            "border: 1px solid rgba(99, 102, 241, 0.4); border-radius: 4px; padding: 2px 6px;"
        )

        top_row.addWidget(self.name_label, stretch=1)
        top_row.addWidget(self.reason_badge)

        self.path_label = ElidedLabel()
        self.path_label.setFont(get_ui_font(11))
        self.path_label.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY}; border: none; background: transparent;")

        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 2, 0, 0)
        info_row.setSpacing(6)

        self.meta_label = QLabel()
        self.meta_label.setFont(get_ui_font(11))
        self.meta_label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED}; border: none; background: transparent;")

        btn_style = (
            f"QPushButton {{ background-color: rgba(255, 255, 255, 0.06); color: {ThemeColors.TEXT_SECONDARY}; "
            f"border: 1px solid {ThemeColors.BORDER}; border-radius: 5px; padding: 2px 8px; font-size: 11px; }}"
            f"QPushButton:hover {{ background-color: rgba(99, 102, 241, 0.2); color: {ThemeColors.TEXT_PRIMARY}; "
            f"border-color: {ThemeColors.BRAND_START}; }}"
        )
        self.open_btn = QPushButton(tr("Open"))
        self.open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_btn.setStyleSheet(btn_style)
        self.open_btn.clicked.connect(self._on_open_clicked)

        self.reveal_btn = QPushButton(tr("Show in Files"))
        self.reveal_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reveal_btn.setStyleSheet(btn_style)
        self.reveal_btn.clicked.connect(self._on_reveal_clicked)

        info_row.addWidget(self.meta_label)
        info_row.addStretch()
        info_row.addWidget(self.open_btn)
        info_row.addWidget(self.reveal_btn)

        layout.addLayout(top_row)
        layout.addWidget(self.path_label)
        layout.addLayout(info_row)

    def set_config(self, config: AppConfig | None) -> None:
        """Update application configuration used when executing hit actions."""
        self._config = config

    def retranslate_ui(self) -> None:
        """Refresh translated button labels and match reason badge."""
        self.open_btn.setText(tr("Open"))
        self.reveal_btn.setText(tr("Show in Files"))
        if self._path or self._reason:
            self.reason_badge.setText(tr(self._reason) if self._reason else tr("Matches query"))

    def update_metadata(
        self,
        name: str,
        path: str,
        size_bytes: int = 0,
        mtime: float = 0.0,
        reason: str = "",
        *,
        hit: SearchHit | None = None,
    ) -> None:
        """Update displayed metadata fields and bind optional SearchHit for deep-link Open."""
        self._path = path
        self._reason = str(reason) if reason else (str(hit.reason) if hit is not None else "")
        self._hit = hit
        self.name_label.setText(name)
        self.path_label.setText(format_tilde_path(path))

        if size_bytes <= 0 and mtime <= 0 and path and Path(path).exists():
            stat = Path(path).stat()
            size_bytes = stat.st_size
            mtime = stat.st_mtime

        meta_parts: list[str] = []
        if size_bytes > 0:
            meta_parts.append(format_file_size(size_bytes))
        if mtime > 0:
            dt = datetime.fromtimestamp(mtime, tz=UTC).astimezone()
            meta_parts.append(dt.strftime("%Y-%m-%d %H:%M"))
        self.meta_label.setText(" • ".join(meta_parts) if meta_parts else tr("Local file"))

        display_reason = tr(self._reason) if self._reason else tr("Matches query")
        self.reason_badge.setText(display_reason)

    def clear(self) -> None:
        """Clear bound search hit and file path."""
        self._path = ""
        self._reason = ""
        self._hit = None

    def _on_open_clicked(self) -> None:
        if self._hit is not None:
            execute_hit_action(self._hit, self._config)
            self.action_triggered.emit()
        elif self._path:
            open_file(self._path)
            self.action_triggered.emit()

    def _on_reveal_clicked(self) -> None:
        target = self._hit.path if self._hit is not None else self._path
        if target:
            reveal_in_file_manager(target)
            self.action_triggered.emit()


class EmptyPreviewWidget(QWidget):
    """Empty placeholder widget when no result is selected."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_label = QLabel("🔍")
        self.icon_label.setFont(get_ui_font(32))
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.text_label = QLabel(tr("Select an item to preview"))
        self.text_label.setFont(get_ui_font(13))
        self.text_label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED};")
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label)

    def retranslate_ui(self) -> None:
        """Refresh translated placeholder label."""
        self.text_label.setText(tr("Select an item to preview"))
