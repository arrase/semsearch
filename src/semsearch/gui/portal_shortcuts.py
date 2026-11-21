"""Wayland global shortcut registration via XDG Desktop Portal GlobalShortcuts over QtDBus."""

import ctypes
import os
from functools import lru_cache
from typing import cast
from uuid import uuid4

from PySide6.QtCore import SLOT, QObject, Signal, Slot
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage, QDBusObjectPath

PORTAL_SERVICE = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
GLOBAL_SHORTCUTS_INTERFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST_INTERFACE = "org.freedesktop.portal.Request"
SESSION_INTERFACE = "org.freedesktop.portal.Session"

SHORTCUT_ID = "toggle-panel"
SHORTCUT_DESCRIPTION = "Toggle SemSearch Quick Panel"


def _dbus_slot(signature: str) -> bytes:
    """Wrap Qt SLOT() str as bytes to match PySide6's QtDBus.pyi const char* stub."""
    return cast(bytes, SLOT(signature))


_CREATE_RESPONSE_SLOT = _dbus_slot("_on_create_session_response(uint,QVariantMap)")
_BIND_RESPONSE_SLOT = _dbus_slot("_on_bind_shortcuts_response(uint,QVariantMap)")
_ACTIVATED_SLOT = _dbus_slot("_on_shortcut_activated(QDBusObjectPath,QString,qulonglong,QVariantMap)")

_DBUS_TYPE_ARRAY = ord("a")
_DBUS_TYPE_DICT_ENTRY = ord("e")
_DBUS_TYPE_OBJECT_PATH = ord("o")
_DBUS_TYPE_STRING = ord("s")
_DBUS_TYPE_STRUCT = ord("r")
_DBUS_TYPE_VARIANT = ord("v")

_MOD_CONTROL = "CTRL"
_MOD_LOGO = "LOGO"

_PORTAL_MODIFIERS: dict[str, str] = {
    "ctrl": _MOD_CONTROL,
    "control": _MOD_CONTROL,
    "primary": _MOD_CONTROL,
    "shift": "SHIFT",
    "alt": "ALT",
    "super": _MOD_LOGO,
    "meta": _MOD_LOGO,
    "cmd": _MOD_LOGO,
    "win": _MOD_LOGO,
    "windows": _MOD_LOGO,
    "logo": _MOD_LOGO,
    "hyper": _MOD_LOGO,
}

_PORTAL_KEYSYMS: dict[str, str] = {
    "space": "space",
    "enter": "Return",
    "return": "Return",
    "esc": "Escape",
    "escape": "Escape",
    "tab": "Tab",
    "backspace": "BackSpace",
    "delete": "Delete",
    "del": "Delete",
    "insert": "Insert",
    "ins": "Insert",
    "home": "Home",
    "end": "End",
    "page_up": "Page_Up",
    "pageup": "Page_Up",
    "pgup": "Page_Up",
    "page_down": "Page_Down",
    "pagedown": "Page_Down",
    "pgdn": "Page_Down",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
}


class _DBusMessageIter(ctypes.Structure):
    """ABI-compatible libdbus DBusMessageIter value container."""

    _fields_ = (
        ("dummy1", ctypes.c_void_p),
        ("dummy2", ctypes.c_void_p),
        ("dummy3", ctypes.c_uint32),
        ("dummy4", ctypes.c_int),
        ("dummy5", ctypes.c_int),
        ("dummy6", ctypes.c_int),
        ("dummy7", ctypes.c_int),
        ("dummy8", ctypes.c_int),
        ("dummy9", ctypes.c_int),
        ("dummy10", ctypes.c_int),
        ("dummy11", ctypes.c_int),
        ("pad1", ctypes.c_int),
        ("pad2", ctypes.c_void_p),
        ("pad3", ctypes.c_void_p),
    )


@lru_cache(maxsize=1)
def _load_libdbus() -> ctypes.CDLL:
    """Load the in-process libdbus-1 shared library already linked by QtDBus."""
    lib = ctypes.CDLL("libdbus-1.so.3")
    lib.dbus_message_new_method_call.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
    lib.dbus_message_new_method_call.restype = ctypes.c_void_p
    lib.dbus_message_iter_init_append.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.dbus_message_iter_init_append.restype = None
    lib.dbus_message_iter_append_basic.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    lib.dbus_message_iter_append_basic.restype = ctypes.c_uint32
    lib.dbus_message_iter_open_container.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p]
    lib.dbus_message_iter_open_container.restype = ctypes.c_uint32
    lib.dbus_message_iter_close_container.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    lib.dbus_message_iter_close_container.restype = ctypes.c_uint32
    lib.dbus_connection_send.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    lib.dbus_connection_send.restype = ctypes.c_uint32
    lib.dbus_connection_flush.argtypes = [ctypes.c_void_p]
    lib.dbus_connection_flush.restype = None
    lib.dbus_message_unref.argtypes = [ctypes.c_void_p]
    lib.dbus_message_unref.restype = None
    return lib


def _append_dbus_str(lib: ctypes.CDLL, iterator: _DBusMessageIter, type_code: int, value: str) -> bool:
    raw = ctypes.c_char_p(value.encode("utf-8"))
    return bool(lib.dbus_message_iter_append_basic(ctypes.byref(iterator), type_code, ctypes.byref(raw)))


def _append_dbus_str_variant_map(lib: ctypes.CDLL, parent: _DBusMessageIter, mapping: dict[str, str]) -> bool:
    arr = _DBusMessageIter()
    if not lib.dbus_message_iter_open_container(ctypes.byref(parent), _DBUS_TYPE_ARRAY, b"{sv}", ctypes.byref(arr)):
        return False
    for key, value in mapping.items():
        entry = _DBusMessageIter()
        variant = _DBusMessageIter()
        ok = (
            bool(
                lib.dbus_message_iter_open_container(
                    ctypes.byref(arr), _DBUS_TYPE_DICT_ENTRY, None, ctypes.byref(entry)
                )
            )
            and _append_dbus_str(lib, entry, _DBUS_TYPE_STRING, key)
            and bool(
                lib.dbus_message_iter_open_container(
                    ctypes.byref(entry), _DBUS_TYPE_VARIANT, b"s", ctypes.byref(variant)
                )
            )
            and _append_dbus_str(lib, variant, _DBUS_TYPE_STRING, value)
            and bool(lib.dbus_message_iter_close_container(ctypes.byref(entry), ctypes.byref(variant)))
            and bool(lib.dbus_message_iter_close_container(ctypes.byref(arr), ctypes.byref(entry)))
        )
        if not ok:
            return False
    return bool(lib.dbus_message_iter_close_container(ctypes.byref(parent), ctypes.byref(arr)))


def _append_dbus_shortcuts(
    lib: ctypes.CDLL,
    parent: _DBusMessageIter,
    shortcuts: list[tuple[str, dict[str, str]]],
) -> bool:
    arr = _DBusMessageIter()
    if not lib.dbus_message_iter_open_container(
        ctypes.byref(parent),
        _DBUS_TYPE_ARRAY,
        b"(sa{sv})",
        ctypes.byref(arr),
    ):
        return False
    for shortcut_id, options in shortcuts:
        item = _DBusMessageIter()
        ok = (
            bool(lib.dbus_message_iter_open_container(ctypes.byref(arr), _DBUS_TYPE_STRUCT, None, ctypes.byref(item)))
            and _append_dbus_str(lib, item, _DBUS_TYPE_STRING, shortcut_id)
            and _append_dbus_str_variant_map(lib, item, options)
            and bool(lib.dbus_message_iter_close_container(ctypes.byref(arr), ctypes.byref(item)))
        )
        if not ok:
            return False
    return bool(lib.dbus_message_iter_close_container(ctypes.byref(parent), ctypes.byref(arr)))


def _send_bind_shortcuts(
    bus: QDBusConnection,
    session_handle: str,
    shortcuts: list[tuple[str, dict[str, str]]],
    handle_token: str,
) -> bool:
    """Send GlobalShortcuts.BindShortcuts(o, a(sa{sv}), s, a{sv}) over Qt's active D-Bus connection.

    PySide6 does not expose qDBusRegisterMetaType, so QDBusArgument cannot marshal custom
    array-of-struct signatures like a(sa{sv}). Writing the message via Qt's underlying
    libdbus DBusConnection* preserves the exact unique bus name required by xdg-desktop-portal.
    """
    conn_address = int(bus.internalPointer())
    if conn_address == 0:
        return False
    lib = _load_libdbus()
    msg = lib.dbus_message_new_method_call(
        PORTAL_SERVICE.encode("utf-8"),
        PORTAL_PATH.encode("utf-8"),
        GLOBAL_SHORTCUTS_INTERFACE.encode("utf-8"),
        b"BindShortcuts",
    )
    if not msg:
        return False
    try:
        root = _DBusMessageIter()
        lib.dbus_message_iter_init_append(msg, ctypes.byref(root))
        ok = (
            _append_dbus_str(lib, root, _DBUS_TYPE_OBJECT_PATH, session_handle)
            and _append_dbus_shortcuts(lib, root, shortcuts)
            and _append_dbus_str(lib, root, _DBUS_TYPE_STRING, "")
            and _append_dbus_str_variant_map(lib, root, {"handle_token": handle_token})
        )
        if not ok:
            return False
        conn_ptr = ctypes.c_void_p(conn_address)
        sent = bool(lib.dbus_connection_send(conn_ptr, msg, None))
        if sent:
            lib.dbus_connection_flush(conn_ptr)
        return sent
    finally:
        lib.dbus_message_unref(msg)


def _to_portal_keysym(token: str) -> str:
    if token in _PORTAL_KEYSYMS:
        return _PORTAL_KEYSYMS[token]
    if token.startswith("f") and token[1:].isdigit():
        return token.upper()
    return token


def _build_request_path(sender_name: str, handle_token: str) -> str:
    sanitized_sender = sender_name.lstrip(":").replace(".", "_")
    return f"/org/freedesktop/portal/desktop/request/{sanitized_sender}/{handle_token}"


def is_wayland_session() -> bool:
    """Return True when running inside a Wayland desktop session."""
    return os.environ.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland" or bool(
        os.environ.get("WAYLAND_DISPLAY", "").strip()
    )


def format_portal_trigger(shortcut: str) -> str:
    """Convert shortcut string into XDG Shortcuts Specification format (e.g. 'CTRL+SHIFT+space')."""
    stripped = shortcut.strip()
    raw_parts = stripped.split("+")
    if not stripped or any(not part.strip() for part in raw_parts):
        raise ValueError(f"Malformed shortcut: {shortcut!r}")

    modifiers: list[str] = []
    keys: list[str] = []
    for raw_part in raw_parts:
        token = raw_part.strip().removeprefix("<").removesuffix(">").strip().lower()
        if not token or "<" in token or ">" in token:
            raise ValueError(f"Malformed shortcut token in {shortcut!r}")
        if token in _PORTAL_MODIFIERS:
            modifier = _PORTAL_MODIFIERS[token]
            if modifier not in modifiers:
                modifiers.append(modifier)
        else:
            keys.append(_to_portal_keysym(token))

    if not modifiers:
        raise ValueError(f"Shortcut must include at least one modifier: {shortcut!r}")
    if len(keys) != 1:
        raise ValueError(f"Shortcut must include exactly one key: {shortcut!r}")
    return "+".join([*modifiers, keys[0]])


class PortalShortcutListener(QObject):
    """Registers and listens for a Wayland global shortcut via XDG Desktop Portal."""

    hotkey_triggered = Signal()
    registration_failed = Signal(str)

    def __init__(self, shortcut: str = "ctrl+shift+space", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.shortcut = shortcut
        self.last_error = ""
        self._bus: QDBusConnection | None = None
        self._portal_iface: QDBusInterface | None = None
        self._session_handle = ""
        self._create_request_path = ""
        self._bind_request_path = ""
        self._activated_connected = False
        self._trigger = ""

    def start(self) -> bool:
        """Open a GlobalShortcuts portal session and initiate shortcut binding."""
        if self._bus is not None:
            return True

        opened = self._open_portal_interface()
        if opened is None:
            return False
        bus, portal, trigger = opened

        handle_token = f"semsearch_{uuid4().hex}"
        session_token = f"semsearch_session_{uuid4().hex}"
        request_path = _build_request_path(bus.baseService(), handle_token)
        if not bus.connect(
            PORTAL_SERVICE,
            request_path,
            REQUEST_INTERFACE,
            "Response",
            self,
            _CREATE_RESPONSE_SLOT,
        ):
            self._fail("Failed to subscribe to portal CreateSession response")
            return False

        self._bus = bus
        self._portal_iface = portal
        self._create_request_path = request_path
        self._trigger = trigger
        self.last_error = ""

        options: dict[str, str] = {
            "handle_token": handle_token,
            "session_handle_token": session_token,
        }
        reply = portal.call("CreateSession", options)
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            reason = reply.errorMessage() or reply.errorName() or "Portal CreateSession call failed"
            self.stop()
            self._fail(reason)
            return False

        return True

    def _open_portal_interface(self) -> tuple[QDBusConnection, QDBusInterface, str] | None:
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            self._fail("D-Bus session bus is not available")
            return None

        portal = QDBusInterface(PORTAL_SERVICE, PORTAL_PATH, GLOBAL_SHORTCUTS_INTERFACE, bus)
        if not portal.isValid() or portal.lastError().isValid():
            reason = portal.lastError().message() or "XDG Desktop Portal GlobalShortcuts interface is not available"
            self._fail(reason)
            return None

        if not bus.baseService():
            self._fail("D-Bus session bus has no unique service name")
            return None

        try:
            trigger = format_portal_trigger(self.shortcut)
        except ValueError as exc:
            self._fail(str(exc))
            return None

        return bus, portal, trigger

    def stop(self) -> None:
        """Disconnect portal D-Bus signals and close the active portal session."""
        self._disconnect_create_response()
        self._disconnect_bind_response()
        if self._bus is not None and self._activated_connected:
            self._bus.disconnect(
                PORTAL_SERVICE,
                PORTAL_PATH,
                GLOBAL_SHORTCUTS_INTERFACE,
                "Activated",
                self,
                _ACTIVATED_SLOT,
            )
            self._activated_connected = False

        if self._bus is not None and self._session_handle:
            close_msg = QDBusMessage.createMethodCall(
                PORTAL_SERVICE,
                self._session_handle,
                SESSION_INTERFACE,
                "Close",
            )
            self._bus.send(close_msg)

        self._session_handle = ""
        self._portal_iface = None
        self._bus = None

    @Slot("uint", "QVariantMap")
    def _on_create_session_response(self, response_code: int, results: dict[str, object]) -> None:
        self._disconnect_create_response()
        if self._bus is None:
            return
        if response_code != 0:
            self._fail(f"Portal CreateSession failed with response code {response_code}")
            return

        raw_handle = results.get("session_handle")
        if isinstance(raw_handle, QDBusObjectPath):
            session_handle = raw_handle.path()
        elif isinstance(raw_handle, str):
            session_handle = raw_handle
        else:
            session_handle = ""

        if not session_handle:
            self._fail("Portal CreateSession response did not include session_handle")
            return

        self._session_handle = session_handle
        self._bind_session_shortcut(session_handle)

    @Slot("uint", "QVariantMap")
    def _on_bind_shortcuts_response(self, response_code: int, _results: dict[str, object]) -> None:
        self._disconnect_bind_response()
        if response_code != 0:
            self._fail(f"Portal BindShortcuts failed with response code {response_code}")

    @Slot(QDBusObjectPath, str, "qulonglong", "QVariantMap")
    def _on_shortcut_activated(
        self,
        session_handle: QDBusObjectPath,
        shortcut_id: str,
        _timestamp: int,
        _options: dict[str, object],
    ) -> None:
        if session_handle.path() == self._session_handle and shortcut_id == SHORTCUT_ID:
            self.hotkey_triggered.emit()

    def _bind_session_shortcut(self, session_handle: str) -> None:
        if self._bus is None:
            return
        if not self._bus.connect(
            PORTAL_SERVICE,
            PORTAL_PATH,
            GLOBAL_SHORTCUTS_INTERFACE,
            "Activated",
            self,
            _ACTIVATED_SLOT,
        ):
            self._fail("Failed to subscribe to portal GlobalShortcuts Activated signal")
            return
        self._activated_connected = True

        bind_token = f"semsearch_bind_{uuid4().hex}"
        bind_request_path = _build_request_path(self._bus.baseService(), bind_token)
        if self._bus.connect(
            PORTAL_SERVICE,
            bind_request_path,
            REQUEST_INTERFACE,
            "Response",
            self,
            _BIND_RESPONSE_SLOT,
        ):
            self._bind_request_path = bind_request_path

        shortcuts: list[tuple[str, dict[str, str]]] = [
            (
                SHORTCUT_ID,
                {
                    "description": SHORTCUT_DESCRIPTION,
                    "preferred_trigger": self._trigger,
                },
            )
        ]
        if not _send_bind_shortcuts(self._bus, session_handle, shortcuts, bind_token):
            self._disconnect_bind_response()
            self._fail("Failed to send BindShortcuts request to portal")

    def _disconnect_create_response(self) -> None:
        if self._bus is not None and self._create_request_path:
            self._bus.disconnect(
                PORTAL_SERVICE,
                self._create_request_path,
                REQUEST_INTERFACE,
                "Response",
                self,
                _CREATE_RESPONSE_SLOT,
            )
            self._create_request_path = ""

    def _disconnect_bind_response(self) -> None:
        if self._bus is not None and self._bind_request_path:
            self._bus.disconnect(
                PORTAL_SERVICE,
                self._bind_request_path,
                REQUEST_INTERFACE,
                "Response",
                self,
                _BIND_RESPONSE_SLOT,
            )
            self._bind_request_path = ""

    def _fail(self, reason: str) -> None:
        self.last_error = reason
        self.registration_failed.emit(reason)
