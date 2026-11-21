"""Audio and video preview renderers with QtMultimedia playback, RMS waveform, and cached frames."""

from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, QSignalBlocker, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPaintEvent, QPixmap, QResizeEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget

from semsearch.config import AppConfig
from semsearch.core.types import SearchHit
from semsearch.extractors.media import probe_media_duration
from semsearch.gui.preview.loader import (
    PreviewLoader,
    extract_video_frame_cached,
    extract_waveform_bars,
)
from semsearch.gui.preview.metadata import MetadataCard
from semsearch.gui.theme import ThemeColors, get_ui_font
from semsearch.i18n import tr
from semsearch.services.actions import open_media_at

_SLIDER_STEPS = 1000
_WAVEFORM_BAR_COUNT = 64
_DEFAULT_BAR_LEVEL = 0.25
_SCRUB_DEBOUNCE_MS = 150
_PADDING_PX = 12
_PLAY_LABEL = "▶ Play"
_PAUSE_LABEL = "⏸ Pause"
_POSTER_FALLBACK_SIZE = QSize(390, 220)
_POSTER_RENDER_SIZE = QSize(480, 270)


def _format_mmss(seconds: float) -> str:
    total = max(0, int(seconds))
    mins, secs = divmod(total, 60)
    return f"{mins}:{secs:02d}"


class WaveformWidget(QWidget):
    """64-bar RMS audio amplitude waveform visualization with active progress indicator."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(80)
        self._progress: float = 0.0
        self._bars: list[float] = [_DEFAULT_BAR_LEVEL] * _WAVEFORM_BAR_COUNT

    def set_progress(self, progress: float) -> None:
        """Update active progress in [0.0, 1.0]."""
        self._progress = max(0.0, min(progress, 1.0))
        self.update()

    def set_bars(self, bars: list[float]) -> None:
        """Update waveform bar amplitudes in [0.0, 1.0]."""
        if bars:
            self._bars = [max(0.05, min(1.0, float(v))) for v in bars]
        else:
            self._bars = [_DEFAULT_BAR_LEVEL] * _WAVEFORM_BAR_COUNT
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        """Paint rounded amplitude bars colored up to active playback progress."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width = self.width()
        height = self.height()
        bar_count = len(self._bars)
        if bar_count == 0 or width <= 0 or height <= 0:
            painter.end()
            return

        gap = 2.0
        bar_width = max(1.0, (width - (bar_count - 1) * gap) / bar_count)
        active_count = int(self._progress * bar_count)

        for i, level in enumerate(self._bars):
            bar_h = level * height
            x = i * (bar_width + gap)
            y = (height - bar_h) / 2.0
            color = QColor(ThemeColors.BRAND_START) if i <= active_count else QColor(ThemeColors.TEXT_MUTED)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawRoundedRect(QRectF(x, y, bar_width, bar_h), 1.5, 1.5)

        painter.end()


class _MediaPreviewBase(QWidget):
    """Shared QtMultimedia playback state, duration probing, and timeline controls for media previews."""

    action_triggered = Signal()

    def __init__(
        self,
        config: AppConfig | None = None,
        loader: PreviewLoader | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._loader = loader if loader is not None else PreviewLoader(self)
        self._owns_loader = loader is None

        self.scrub_slider = QSlider(Qt.Orientation.Horizontal)
        self.scrub_slider.setRange(0, _SLIDER_STEPS)

        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setFont(get_ui_font(11))
        self.time_label.setStyleSheet(f"color: {ThemeColors.TEXT_SECONDARY};")

        self.meta_card = MetadataCard(config=config)

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.durationChanged.connect(self._on_player_duration_changed)

        self._path: str = ""
        self._duration: float = 0.0
        self._current_pos: float = 0.0

    def _on_probed_duration_ready(self, payload: object) -> None:
        if isinstance(payload, (int, float)) and payload > 0.0:
            self._apply_duration(float(payload))

    def _on_player_duration_changed(self, duration_ms: int) -> None:
        if duration_ms > 0:
            self._apply_duration(duration_ms / 1000.0)

    def _apply_duration(self, seconds: float) -> None:
        if seconds <= 0.0:
            return
        self._duration = max(seconds, self._current_pos)
        self._sync_position_ui()

    def _sync_slider_and_time(self) -> float:
        ratio = self._current_pos / self._duration if self._duration > 0.0 else 0.0
        clamped = max(0.0, min(1.0, ratio))
        with QSignalBlocker(self.scrub_slider):
            self.scrub_slider.setValue(int(clamped * _SLIDER_STEPS))
        self._update_time_label()
        return clamped

    def _sync_position_ui(self) -> None:
        self._sync_slider_and_time()

    def _update_time_label(self) -> None:
        self.time_label.setText(f"{_format_mmss(self._current_pos)} / {_format_mmss(self._duration)}")

    def clear(self) -> None:
        """Stop playback, release media source, and reset shared timeline state."""
        self.player.stop()
        self.player.setSource(QUrl())
        self._path = ""
        self._current_pos = 0.0
        self._duration = 0.0
        with QSignalBlocker(self.scrub_slider):
            self.scrub_slider.setValue(0)
        self._update_time_label()
        self.meta_card.clear()

    def shutdown(self) -> None:
        """Clear media resources and wait for owned loader pool when standalone."""
        self.clear()
        if self._owns_loader:
            self._loader.shutdown()


class AudioPreviewWidget(_MediaPreviewBase):
    """Audio preview widget with in-panel QtMultimedia playback and real RMS waveform."""

    def __init__(
        self,
        config: AppConfig | None = None,
        loader: PreviewLoader | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(config=config, loader=loader, parent=parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        self.waveform = WaveformWidget()

        controls = QHBoxLayout()
        self.play_btn = QPushButton(tr(_PLAY_LABEL))
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.clicked.connect(self.toggle_playback)

        self.scrub_slider.valueChanged.connect(self._on_scrub)

        controls.addWidget(self.play_btn)
        controls.addWidget(self.scrub_slider, stretch=1)
        controls.addWidget(self.time_label)

        layout.addWidget(self.waveform, stretch=1)
        layout.addLayout(controls)
        layout.addWidget(self.meta_card)

        self.player.positionChanged.connect(self._on_player_position_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)

        self._pending_seek_ms: int | None = None

    def retranslate_ui(self) -> None:
        """Refresh translated button label and metadata card."""
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.play_btn.setText(_PAUSE_LABEL)
        else:
            self.play_btn.setText(tr(_PLAY_LABEL))
        self.meta_card.retranslate_ui()

    def load_audio(
        self,
        path: str,
        name: str,
        loc: float = 0.0,
        *,
        duration: float = 0.0,
        size: int = 0,
        mtime: float = 0.0,
        reason: str = "",
        hit: SearchHit | None = None,
    ) -> None:
        """Load audio result, position scrubber at hit moment, and decode waveform in background."""
        self.stop_playback()
        self._loader.cancel("audio_waveform")
        self._loader.cancel("audio_duration")

        self._path = path
        self._current_pos = max(0.0, loc)
        self._duration = duration if duration > 0.0 else max(self._current_pos, 1.0)
        self._pending_seek_ms = int(self._current_pos * 1000)

        self.waveform.set_bars([])
        self._sync_position_ui()
        self.meta_card.update_metadata(name, path, size, mtime, reason, hit=hit)

        if path and Path(path).is_file():
            self.player.setSource(QUrl.fromLocalFile(path))
            self._loader.submit(
                "audio_waveform",
                lambda: extract_waveform_bars(path, _WAVEFORM_BAR_COUNT),
                self._on_waveform_ready,
            )
            if duration <= 0.0:
                self._loader.submit(
                    "audio_duration",
                    lambda: probe_media_duration(path),
                    self._on_probed_duration_ready,
                )
        else:
            self.player.setSource(QUrl())

    def _on_waveform_ready(self, payload: object) -> None:
        if isinstance(payload, list):
            self.waveform.set_bars([float(v) for v in payload if isinstance(v, (int, float))])

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if self._pending_seek_ms is not None and status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ):
            self.player.setPosition(self._pending_seek_ms)
            self._pending_seek_ms = None

    def _on_player_position_changed(self, position_ms: int) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState:
            return
        self._current_pos = max(0.0, position_ms / 1000.0)
        self._sync_position_ui()

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_btn.setText(_PAUSE_LABEL)
        else:
            self.play_btn.setText(tr(_PLAY_LABEL))

    def toggle_playback(self) -> None:
        """Play or pause audio inside the preview panel starting from current scrub position."""
        if not self._path:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            return
        if Path(self._path).is_file() and self.player.error() == QMediaPlayer.Error.NoError:
            target_url = QUrl.fromLocalFile(self._path)
            if self.player.source() != target_url:
                self.player.setSource(target_url)
            seek_ms = int(self._current_pos * 1000)
            self._pending_seek_ms = seek_ms
            self.player.setPosition(seek_ms)
            self.player.play()
            return
        open_media_at(self._path, self._current_pos)
        self.action_triggered.emit()

    def _toggle_playback(self) -> None:
        self.toggle_playback()

    def stop_playback(self) -> None:
        """Stop active audio playback without clearing current selection metadata."""
        self.player.stop()
        self.play_btn.setText(tr(_PLAY_LABEL))

    def _on_scrub(self, val: int) -> None:
        ratio = val / _SLIDER_STEPS
        self._current_pos = ratio * self._duration
        self._pending_seek_ms = int(self._current_pos * 1000)
        self.waveform.set_progress(ratio)
        self._update_time_label()
        if self.player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
            self.player.setPosition(self._pending_seek_ms)

    def _sync_position_ui(self) -> None:
        self.waveform.set_progress(self._sync_slider_and_time())

    def clear(self) -> None:
        """Stop playback, release media source, cancel async tasks, and reset state."""
        self._loader.cancel("audio_waveform")
        self._loader.cancel("audio_duration")
        super().clear()
        self._pending_seek_ms = None
        self.waveform.set_bars([])
        self.waveform.set_progress(0.0)
        self.play_btn.setText(tr(_PLAY_LABEL))


class VideoPreviewWidget(_MediaPreviewBase):
    """Video preview with async cached ffmpeg thumbnails, debounced scrubbing, and moment jump."""

    def __init__(
        self,
        config: AppConfig | None = None,
        loader: PreviewLoader | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(config=config, loader=loader, parent=parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.poster_label = QLabel()
        self.poster_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.poster_label.setMinimumSize(200, 160)
        self.poster_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.poster_label.setStyleSheet("border-radius: 8px; background-color: rgba(0, 0, 0, 0.4);")

        controls = QHBoxLayout()
        self.scrub_slider.valueChanged.connect(self._on_scrub)

        self.jump_btn = QPushButton(tr("▶ Open at Moment"))
        self.jump_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.jump_btn.clicked.connect(self._jump_to_timestamp)

        controls.addWidget(self.scrub_slider, stretch=1)
        controls.addWidget(self.time_label)
        controls.addWidget(self.jump_btn)

        layout.addWidget(self.poster_label, stretch=1)
        layout.addLayout(controls)
        layout.addWidget(self.meta_card)

        self._scrub_timer = QTimer(self)
        self._scrub_timer.setSingleShot(True)
        self._scrub_timer.setInterval(_SCRUB_DEBOUNCE_MS)
        self._scrub_timer.timeout.connect(self._request_scrub_frame)

        self._poster_pixmap = QPixmap()

    def retranslate_ui(self) -> None:
        """Refresh translated button label and metadata card."""
        self.jump_btn.setText(tr("▶ Open at Moment"))
        self.meta_card.retranslate_ui()

    def _compose_poster_pixmap(self, image: QImage) -> QPixmap:
        if not image.isNull():
            pixmap = QPixmap.fromImage(image).scaled(
                _POSTER_RENDER_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        else:
            pixmap = QPixmap(_POSTER_FALLBACK_SIZE)
            pixmap.fill(QColor("#1A1D24"))

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        center_x = pixmap.width() / 2.0
        center_y = pixmap.height() / 2.0

        painter.setPen(Qt.PenStyle.NoPen)
        badge_color = QColor(ThemeColors.BRAND_START)
        badge_color.setAlpha(215)
        painter.setBrush(QBrush(badge_color))
        painter.drawEllipse(QRectF(center_x - 28, center_y - 28, 56, 56))

        painter.setBrush(QBrush(QColor("#FFFFFF")))
        triangle = [
            QPoint(int(center_x - 7), int(center_y - 13)),
            QPoint(int(center_x + 13), int(center_y)),
            QPoint(int(center_x - 7), int(center_y + 13)),
        ]
        painter.drawPolygon(triangle)
        painter.end()
        return pixmap

    def _set_poster_from_image(self, image: QImage) -> None:
        self._poster_pixmap = self._compose_poster_pixmap(image)
        self._rescale_poster()

    def load_video(
        self,
        path: str,
        name: str,
        loc: float = 0.0,
        *,
        duration: float = 0.0,
        size: int = 0,
        mtime: float = 0.0,
        reason: str = "",
        hit: SearchHit | None = None,
    ) -> None:
        """Load video metadata, display immediate poster, and fetch cached frame asynchronously."""
        self.stop_playback()
        self._loader.cancel("video_frame")
        self._loader.cancel("video_duration")

        self._path = path
        self._current_pos = max(0.0, loc)
        self._duration = duration if duration > 0.0 else max(self._current_pos, 1.0)

        self._set_poster_from_image(QImage())
        self._sync_position_ui()
        self.meta_card.update_metadata(name, path, size, mtime, reason, hit=hit)

        if path and Path(path).is_file():
            self.player.setSource(QUrl.fromLocalFile(path))
            target_ts = self._current_pos
            self._loader.submit(
                "video_frame",
                lambda: extract_video_frame_cached(path, target_ts),
                self._on_frame_ready,
            )
            if duration <= 0.0:
                self._loader.submit(
                    "video_duration",
                    lambda: probe_media_duration(path),
                    self._on_probed_duration_ready,
                )
        else:
            self.player.setSource(QUrl())

    def _on_frame_ready(self, payload: object) -> None:
        if isinstance(payload, QImage) and not payload.isNull():
            self._set_poster_from_image(payload)

    def _rescale_poster(self) -> None:
        if self._poster_pixmap.isNull():
            return
        dpr = max(1.0, self.devicePixelRatioF())
        target = self.poster_label.size() - QSize(_PADDING_PX, _PADDING_PX)
        phys_w = max(1, int(target.width() * dpr))
        phys_h = max(1, int(target.height() * dpr))
        scaled = self._poster_pixmap.scaled(
            QSize(phys_w, phys_h),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)
        self.poster_label.setPixmap(scaled)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Rescale video poster on resize."""
        super().resizeEvent(event)
        self._rescale_poster()

    def _on_scrub(self, val: int) -> None:
        ratio = val / _SLIDER_STEPS
        self._current_pos = ratio * self._duration
        self._update_time_label()
        if self._path and Path(self._path).is_file():
            self._scrub_timer.start()

    def _request_scrub_frame(self) -> None:
        if not self._path or not Path(self._path).is_file():
            return
        target_path = self._path
        target_ts = self._current_pos
        self._loader.submit(
            "video_frame",
            lambda: extract_video_frame_cached(target_path, target_ts),
            self._on_frame_ready,
        )

    def _jump_to_timestamp(self) -> None:
        if self._path:
            open_media_at(self._path, self._current_pos)
            self.action_triggered.emit()

    def stop_playback(self) -> None:
        """Stop scrub debounce timer and media player."""
        self._scrub_timer.stop()
        self.player.stop()

    def clear(self) -> None:
        """Release media resources, cancel thumbnail workers, and reset state."""
        self._scrub_timer.stop()
        self._loader.cancel("video_frame")
        self._loader.cancel("video_duration")
        super().clear()
        self._poster_pixmap = QPixmap()
        self.poster_label.clear()
