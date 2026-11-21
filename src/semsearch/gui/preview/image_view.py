"""Image, SVG, and animated GIF preview renderer with HiDPI scaling."""

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QMovie, QPixmap, QResizeEvent
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from semsearch.config import AppConfig
from semsearch.core.types import SearchHit
from semsearch.gui.preview.loader import PreviewLoader, load_scaled_image
from semsearch.gui.preview.metadata import MetadataCard
from semsearch.gui.theme import ThemeColors, get_ui_font
from semsearch.i18n import tr

_MIN_DECODE_SIDE = 512
_PADDING_PX = 12
_IMAGE_PAYLOAD_LEN = 2


class ImagePreviewWidget(QWidget):
    """Image, SVG, and animated GIF preview renderer with aspect-ratio and HiDPI preservation."""

    def __init__(
        self,
        config: AppConfig | None = None,
        loader: PreviewLoader | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._loader = loader if loader is not None else PreviewLoader(self)
        self._owns_loader = loader is None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumSize(200, 160)
        self.image_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.image_label.setStyleSheet("border-radius: 8px; background-color: rgba(0, 0, 0, 0.2);")

        self.dimensions_label = QLabel()
        self.dimensions_label.setFont(get_ui_font(11))
        self.dimensions_label.setStyleSheet(f"color: {ThemeColors.TEXT_MUTED};")
        self.dimensions_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.meta_card = MetadataCard(config=config)

        layout.addWidget(self.image_label, stretch=1)
        layout.addWidget(self.dimensions_label)
        layout.addWidget(self.meta_card)

        self._pixmap = QPixmap()
        self._movie: QMovie | None = None
        self._movie_orig_size = QSize()

    def retranslate_ui(self) -> None:
        """Refresh translated labels."""
        self.meta_card.retranslate_ui()
        if self._pixmap.isNull() and self._movie is None and self.image_label.text():
            self.image_label.setText(tr("Unable to load image"))

    def _stop_movie(self) -> None:
        if self._movie is not None:
            self._movie.stop()
            self._movie = None
            self._movie_orig_size = QSize()

    def _try_load_animated_gif(self, path: str) -> bool:
        if Path(path).suffix.lower() != ".gif" or not Path(path).is_file():
            return False
        reader = QImageReader(path)
        if not reader.supportsAnimation() or reader.imageCount() == 1:
            return False
        movie = QMovie(path)
        if not movie.isValid():
            return False
        orig_size = reader.size()
        self._pixmap = QPixmap()
        self._movie = movie
        self._movie_orig_size = orig_size
        if orig_size.isValid():
            self.dimensions_label.setText(f"{orig_size.width()} × {orig_size.height()} px")
        else:
            self.dimensions_label.setText("")
        self._rescale_movie()
        self.image_label.setMovie(movie)
        movie.start()
        return True

    def _target_decode_side(self) -> int:
        dpr = max(1.0, self.devicePixelRatioF())
        target = self.image_label.size() - QSize(_PADDING_PX, _PADDING_PX)
        return max(int(max(target.width(), target.height(), _MIN_DECODE_SIDE) * dpr), _MIN_DECODE_SIDE)

    def _apply_loaded_image(self, image: QImage, orig_size: tuple[int, int]) -> None:
        if not image.isNull():
            self._pixmap = QPixmap.fromImage(image)
            orig_w, orig_h = orig_size
            self.dimensions_label.setText(f"{orig_w} × {orig_h} px")
            self._rescale_pixmap()
            return
        self._pixmap = QPixmap()
        self.image_label.clear()
        self.image_label.setText(tr("Unable to load image"))
        self.dimensions_label.setText("")

    def _on_async_image_ready(self, payload: object) -> None:
        if isinstance(payload, tuple) and len(payload) == _IMAGE_PAYLOAD_LEN:
            img, orig_size = payload
            if isinstance(img, QImage) and isinstance(orig_size, tuple):
                self._apply_loaded_image(img, orig_size)

    def load_image(
        self,
        path: str,
        name: str,
        size: int = 0,
        mtime: float = 0.0,
        reason: str = "",
        *,
        hit: SearchHit | None = None,
        async_load: bool = False,
    ) -> None:
        """Load and display raster, SVG, or animated GIF image preserving aspect ratio."""
        self._loader.cancel("image")
        self._stop_movie()
        self.meta_card.update_metadata(name, path, size, mtime, reason, hit=hit)

        if self._try_load_animated_gif(path):
            return

        max_side = self._target_decode_side()
        if async_load:
            self._pixmap = QPixmap()
            self.image_label.clear()
            self.dimensions_label.setText("")
            self._loader.submit(
                "image",
                lambda: load_scaled_image(path, max_side),
                self._on_async_image_ready,
            )
            return

        image, orig_size = load_scaled_image(path, max_side)
        self._apply_loaded_image(image, orig_size)

    def _rescale_pixmap(self) -> None:
        if self._pixmap.isNull():
            return
        dpr = max(1.0, self.devicePixelRatioF())
        target = self.image_label.size() - QSize(_PADDING_PX, _PADDING_PX)
        phys_w = max(1, int(target.width() * dpr))
        phys_h = max(1, int(target.height() * dpr))
        scaled = self._pixmap.scaled(
            QSize(phys_w, phys_h),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)
        self.image_label.setPixmap(scaled)

    def _rescale_movie(self) -> None:
        if self._movie is None or not self._movie_orig_size.isValid():
            return
        target = self.image_label.size() - QSize(_PADDING_PX, _PADDING_PX)
        scaled_size = self._movie_orig_size.scaled(
            max(1, target.width()),
            max(1, target.height()),
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        self._movie.setScaledSize(scaled_size)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Rescale current pixmap or movie cleanly on widget resize."""
        super().resizeEvent(event)
        self._rescale_pixmap()
        self._rescale_movie()

    def clear(self) -> None:
        """Release cached image pixmap, stop animations, and clear labels."""
        self._loader.cancel("image")
        self._stop_movie()
        self._pixmap = QPixmap()
        self.image_label.clear()
        self.dimensions_label.clear()
        self.meta_card.clear()

    def shutdown(self) -> None:
        """Clear preview state and wait for owned background loader when standalone."""
        self.clear()
        if self._owns_loader:
            self._loader.shutdown()
