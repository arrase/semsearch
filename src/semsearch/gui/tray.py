"""System tray manager and dynamic circular progress ring rendering."""

from PySide6.QtCore import QObject, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from semsearch.core.types import EngineActivity, EngineStatus
from semsearch.gui.theme import ThemeColors
from semsearch.i18n import tr
from semsearch.services.indexer import IndexService


def render_tray_icon(percent: float = 0.0, is_indexing: bool = False, is_paused: bool = False) -> QIcon:
    """Render dynamic 64x64 pixmap icon for system tray."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    bounds = QRectF(10, 10, 44, 44)

    if is_paused:
        pen = QPen(QColor(ThemeColors.AMBER), 4.5)
        painter.setPen(pen)
        painter.drawEllipse(bounds)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(ThemeColors.AMBER)))
        painter.drawRoundedRect(QRectF(22, 22, 6, 20), 2, 2)
        painter.drawRoundedRect(QRectF(36, 22, 6, 20), 2, 2)
    elif is_indexing:
        track_pen = QPen(QColor("#2E323D"), 4.5)
        painter.setPen(track_pen)
        painter.drawEllipse(bounds)

        sweep_pen = QPen(QColor(ThemeColors.LEAF), 4.5)
        sweep_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(sweep_pen)

        start_angle = 90 * 16
        clamped_percent = max(0.0, min(percent, 100.0))
        span_angle = int(-360.0 * (clamped_percent / 100.0) * 16)
        painter.drawArc(bounds, start_angle, span_angle)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(ThemeColors.BRAND_START)))
        painter.drawEllipse(QRectF(28, 28, 8, 8))
    else:
        brand_pen = QPen(QColor(ThemeColors.BRAND_START), 4.5)
        brand_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(brand_pen)
        painter.drawEllipse(QRectF(14, 14, 26, 26))
        painter.drawLine(34, 34, 48, 48)

    painter.end()
    return QIcon(pixmap)


class SystemTrayManager(QObject):
    """Coordinates system tray icon state, progress ring, and context menu."""

    open_panel_requested = Signal()
    open_window_requested = Signal()
    open_settings_requested = Signal()
    toggle_pause_requested = Signal()
    quit_requested = Signal()

    def __init__(
        self,
        index_service: IndexService | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.index_service = index_service

        self.tray_icon = QSystemTrayIcon(self)
        self.menu = QMenu()

        self.status_action = QAction(tr("Idle"), self)
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()

        self.panel_action = QAction(tr("Quick Search"), self)
        self.panel_action.triggered.connect(self.open_panel_requested.emit)
        self.menu.addAction(self.panel_action)

        self.window_action = QAction(tr("Search Window"), self)
        self.window_action.triggered.connect(self.open_window_requested.emit)
        self.menu.addAction(self.window_action)

        self.pause_action = QAction(tr("Pause Indexing"), self)
        self.pause_action.triggered.connect(self._handle_toggle_pause)
        self.menu.addAction(self.pause_action)
        self.menu.addSeparator()

        self.settings_action = QAction(tr("Settings..."), self)
        self.settings_action.triggered.connect(self.open_settings_requested.emit)
        self.menu.addAction(self.settings_action)

        self.quit_action = QAction(tr("Quit"), self)
        self.quit_action.triggered.connect(self.quit_requested.emit)
        self.menu.addAction(self.quit_action)

        self.tray_icon.setContextMenu(self.menu)
        self.tray_icon.activated.connect(self._on_tray_activated)

        self.tray_icon.setIcon(render_tray_icon(0.0, is_indexing=False, is_paused=False))
        self.tray_icon.setToolTip("SemSearch")

        self._poll_timer: QTimer | None = None
        if self.index_service is not None:
            self._poll_timer = QTimer(self)
            self._poll_timer.timeout.connect(self._poll_status)
            self._poll_timer.start(1000)

    def retranslate_ui(self) -> None:
        """Refresh translated action labels."""
        self.panel_action.setText(tr("Quick Search"))
        self.window_action.setText(tr("Search Window"))
        self.settings_action.setText(tr("Settings..."))
        self.quit_action.setText(tr("Quit"))
        if self.index_service is not None:
            self._poll_status()
        else:
            self.status_action.setText(tr("Idle"))
            self.pause_action.setText(tr("Pause Indexing"))

    def _poll_status(self) -> None:
        if self.index_service is not None:
            status = (
                self.index_service.get_status()
                if isinstance(self.index_service, IndexService)
                else self.index_service.status
            )
            self.update_status(status)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_panel_requested.emit()

    def _handle_toggle_pause(self) -> None:
        if self.index_service is not None:
            if self.index_service.status.paused:
                self.index_service.resume()
            else:
                self.index_service.pause()
            self.update_status(self.index_service.status)
        self.toggle_pause_requested.emit()

    def update_status(self, status: EngineStatus) -> None:
        """Update system tray icon, tooltip, and menu state from EngineStatus."""
        percent = (status.indexed / status.total * 100.0) if status.total > 0 else 0.0
        is_indexing = status.activity == EngineActivity.INDEXING
        is_paused = status.paused or status.activity == EngineActivity.PAUSED

        if is_paused:
            status_text = tr("Paused")
            self.pause_action.setText(tr("Resume Indexing"))
        elif is_indexing:
            status_text = tr(
                "Indexing: {percent}% ({indexed}/{total})",
                percent=int(percent),
                indexed=status.indexed,
                total=status.total,
            )
            self.pause_action.setText(tr("Pause Indexing"))
        else:
            status_text = (
                tr("Idle - {indexed} indexed", indexed=f"{status.indexed:,}")
                if status.indexed > 0
                else tr("Idle")
            )
            self.pause_action.setText(tr("Pause Indexing"))

        self.status_action.setText(status_text)
        self.tray_icon.setToolTip(f"SemSearch - {status_text}")
        self.tray_icon.setIcon(render_tray_icon(percent, is_indexing=is_indexing, is_paused=is_paused))

    def show(self) -> None:
        """Display tray icon."""
        self.tray_icon.show()

    def hide(self) -> None:
        """Hide tray icon."""
        self.tray_icon.hide()
