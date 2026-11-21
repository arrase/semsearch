"""Global keyboard shortcut listener supporting Wayland XDG Portal and X11 pynput backends."""

import os
from typing import Any

from PySide6.QtCore import QObject, Signal

from semsearch.gui.portal_shortcuts import PortalShortcutListener, is_wayland_session

try:
    from pynput import keyboard as _pynput_keyboard
except ImportError:
    _pynput_keyboard = None

keyboard: Any = _pynput_keyboard

_KEY_ALIASES: dict[str, str] = {
    "super": "cmd",
    "meta": "cmd",
    "win": "cmd",
    "windows": "cmd",
    "control": "ctrl",
    "return": "enter",
    "escape": "esc",
}

_MODIFIER_KEYS: frozenset[str] = frozenset({"ctrl", "shift", "alt", "alt_gr", "cmd"})


def normalize_hotkey(shortcut: str) -> str:
    """Format shortcut string for pynput GlobalHotKeys syntax (e.g. '<ctrl>+<shift>+k')."""
    stripped = shortcut.strip()
    raw_parts = stripped.split("+")
    if not stripped or any(not part.strip() for part in raw_parts):
        raise ValueError(f"Malformed hotkey: {shortcut!r}")

    modifiers: list[str] = []
    keys: list[str] = []
    for raw_part in raw_parts:
        token = raw_part.strip().removeprefix("<").removesuffix(">").strip().lower()
        if not token or "<" in token or ">" in token:
            raise ValueError(f"Malformed hotkey token in {shortcut!r}")
        canonical = _KEY_ALIASES.get(token, token)
        if canonical in _MODIFIER_KEYS:
            if canonical not in modifiers:
                modifiers.append(canonical)
        else:
            keys.append(canonical)

    if not modifiers:
        raise ValueError(f"Hotkey must include at least one modifier: {shortcut!r}")
    if len(keys) != 1:
        raise ValueError(f"Hotkey must include exactly one key: {shortcut!r}")

    ordered = modifiers + keys
    return "+".join(f"<{part}>" if len(part) > 1 else part for part in ordered)


class HotkeyListener(QObject):
    """Listens for global shortcut combinations via Wayland Portal or X11 pynput."""

    hotkey_triggered = Signal()
    status_changed = Signal(str)

    def __init__(self, shortcut: str = "ctrl+shift+space", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.shortcut = shortcut
        self._portal: PortalShortcutListener | None = None
        self._listener: Any = None
        self._backend_name = "none"
        self._last_error = ""

    @property
    def backend_name(self) -> str:
        """Return active hotkey backend name ('portal', 'pynput', or 'none')."""
        return self._backend_name

    @property
    def last_error(self) -> str:
        """Return the last registration error message, or empty string when active."""
        return self._last_error

    def start(self) -> bool:
        """Start listening for the configured global hotkey."""
        if self._portal is not None or self._listener is not None:
            return True

        portal_error = ""
        if is_wayland_session():
            started, portal_error = self._start_portal()
            if started:
                return True

        return self._start_pynput(portal_error)

    def stop(self) -> None:
        """Stop listening for global hotkey."""
        was_active = self._backend_name != "none"
        if self._portal is not None:
            self._portal.stop()
            self._portal.deleteLater()
            self._portal = None
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self._backend_name = "none"
        if was_active:
            self.status_changed.emit(self._backend_name)

    def _start_portal(self) -> tuple[bool, str]:
        portal = PortalShortcutListener(self.shortcut, parent=self)
        failures: list[str] = []
        portal.registration_failed.connect(failures.append)
        if portal.start():
            portal.registration_failed.disconnect(failures.append)
            portal.registration_failed.connect(self._on_portal_failed)
            portal.hotkey_triggered.connect(self.hotkey_triggered)
            self._portal = portal
            self._backend_name = "portal"
            self._last_error = ""
            self.status_changed.emit(self._backend_name)
            return True, ""

        portal.deleteLater()
        error = failures[-1] if failures else "Wayland GlobalShortcuts portal unavailable"
        return False, error

    def _start_pynput(self, prior_error: str) -> bool:
        if not os.environ.get("DISPLAY", "").strip():
            error = prior_error or "No X11 DISPLAY available for global hotkey listener"
            self._set_failed(error)
            return False

        if keyboard is None:
            error = (
                f"{prior_error}; pynput X11 backend unavailable" if prior_error else "pynput X11 backend unavailable"
            )
            self._set_failed(error)
            return False

        try:
            normalized = normalize_hotkey(self.shortcut)
            listener = keyboard.GlobalHotKeys({normalized: self._on_triggered})
            listener.daemon = True
            listener.start()
        except Exception as exc:  # noqa: BLE001 - native X11/pynput library boundary
            error = f"{prior_error}; X11 hotkey failed: {exc}" if prior_error else str(exc)
            self._set_failed(error)
            return False

        self._listener = listener
        self._backend_name = "pynput"
        self._last_error = ""
        self.status_changed.emit(self._backend_name)
        return True

    def _set_failed(self, error: str) -> None:
        self._backend_name = "none"
        self._last_error = error
        self.status_changed.emit(self._backend_name)

    def _on_portal_failed(self, reason: str) -> None:
        if self._portal is not None:
            self._portal.stop()
            self._portal.deleteLater()
            self._portal = None
        self._set_failed(reason)

    def _on_triggered(self) -> None:
        """Callback invoked by pynput background thread."""
        self.hotkey_triggered.emit()
