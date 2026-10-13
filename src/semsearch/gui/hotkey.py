"""Global keyboard shortcut listener using pynput with thread-safe Qt signaling."""

from pynput import keyboard
from PySide6.QtCore import QObject, Signal


def normalize_hotkey(shortcut: str) -> str:
    """Format shortcut string for pynput GlobalHotKeys syntax (e.g. '<ctrl>+<shift>+k')."""
    clean = shortcut.replace("<", "").replace(">", "").strip().lower()
    parts = [p.strip() for p in clean.split("+") if p.strip()]
    return "+".join(f"<{p}>" if len(p) > 1 else p for p in parts)


class HotkeyListener(QObject):
    """Listens for global shortcut combinations and emits Qt signals."""

    hotkey_triggered = Signal()

    def __init__(self, shortcut: str = "ctrl+shift+space", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.shortcut = shortcut
        self._listener: keyboard.GlobalHotKeys | None = None

    def start(self) -> None:
        """Start listening for global hotkey."""
        if self._listener is not None:
            return

        normalized = normalize_hotkey(self.shortcut)
        self._listener = keyboard.GlobalHotKeys({normalized: self._on_triggered})
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        """Stop listening for global hotkey."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def _on_triggered(self) -> None:
        """Callback invoked by pynput background thread."""
        self.hotkey_triggered.emit()
