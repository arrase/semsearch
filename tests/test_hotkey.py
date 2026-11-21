"""Unit tests for global hotkeys (Wayland XDG Portal, X11 pynput) and app SVG icon."""

from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QSize, QXmlStreamReader, Signal
from PySide6.QtDBus import QDBusConnection, QDBusMessage, QDBusObjectPath, QDBusVariant
from PySide6.QtGui import QIcon
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

import semsearch.gui.hotkey as hotkey_module
import semsearch.gui.portal_shortcuts as portal_module
from semsearch.gui.hotkey import HotkeyListener, normalize_hotkey
from semsearch.gui.portal_shortcuts import (
    GLOBAL_SHORTCUTS_INTERFACE,
    PORTAL_PATH,
    PORTAL_SERVICE,
    REQUEST_INTERFACE,
    SESSION_INTERFACE,
    SHORTCUT_DESCRIPTION,
    SHORTCUT_ID,
    PortalShortcutListener,
    format_portal_trigger,
    is_wayland_session,
)


class _FakeDBusError:
    def __init__(self, *, valid: bool = False, message: str = "") -> None:
        self._valid = valid
        self._message = message

    def isValid(self) -> bool:  # noqa: N802
        return self._valid

    def message(self) -> str:
        return self._message


class _FakeDBusReply:
    def __init__(
        self,
        msg_type: QDBusMessage.MessageType = QDBusMessage.MessageType.ReplyMessage,
        *,
        error_name: str = "",
        error_message: str = "",
    ) -> None:
        self._type = msg_type
        self._error_name = error_name
        self._error_message = error_message

    def type(self) -> QDBusMessage.MessageType:
        return self._type

    def errorName(self) -> str:  # noqa: N802
        return self._error_name

    def errorMessage(self) -> str:  # noqa: N802
        return self._error_message


class _FakeDBusConnection:
    def __init__(self, *, connected: bool = True, base_service: str = ":1.42") -> None:
        self._connected = connected
        self._base_service = base_service
        self.connected_signals: list[tuple[object, ...]] = []
        self.disconnected_signals: list[tuple[object, ...]] = []
        self.sent_messages: list[QDBusMessage] = []

    def isConnected(self) -> bool:  # noqa: N802
        return self._connected

    def baseService(self) -> str:  # noqa: N802
        return self._base_service

    def connect(self, *args: object) -> bool:
        self.connected_signals.append(args)
        return True

    def disconnect(self, *args: object) -> bool:
        self.disconnected_signals.append(args)
        return True

    def send(self, message: QDBusMessage) -> bool:
        self.sent_messages.append(message)
        return True


class _FakeDBusInterface:
    def __init__(
        self,
        *,
        valid: bool = True,
        error: _FakeDBusError | None = None,
        reply: _FakeDBusReply | None = None,
    ) -> None:
        self._valid = valid
        self._error = error if error is not None else _FakeDBusError()
        self._reply = reply if reply is not None else _FakeDBusReply()
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def isValid(self) -> bool:  # noqa: N802
        return self._valid

    def lastError(self) -> _FakeDBusError:  # noqa: N802
        return self._error

    def call(self, method: str, *args: object) -> _FakeDBusReply:
        self.calls.append((method, args))
        return self._reply


class _FakeGlobalHotKeys:
    def __init__(self, hotkeys: dict[str, Callable[[], None]]) -> None:
        self.hotkeys = hotkeys
        self.daemon = False
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def fire_first(self) -> None:
        callback = next(iter(self.hotkeys.values()))
        callback()


class _FakeKeyboardModule:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.instances: list[_FakeGlobalHotKeys] = []

    def GlobalHotKeys(self, hotkeys: dict[str, Callable[[], None]]) -> _FakeGlobalHotKeys:  # noqa: N802
        if self.error is not None:
            raise self.error
        instance = _FakeGlobalHotKeys(hotkeys)
        self.instances.append(instance)
        return instance


class _FakePortalShortcutListener(QObject):
    hotkey_triggered = Signal()
    registration_failed = Signal(str)

    def __init__(
        self,
        shortcut: str = "ctrl+shift+space",
        parent: QObject | None = None,
        *,
        succeed: bool = True,
        failure_reason: str = "Portal unavailable",
    ) -> None:
        super().__init__(parent)
        self.shortcut = shortcut
        self._succeed = succeed
        self._failure_reason = failure_reason
        self.started = False
        self.stopped = False

    def start(self) -> bool:
        self.started = True
        if not self._succeed:
            self.registration_failed.emit(self._failure_reason)
            return False
        return True

    def stop(self) -> None:
        self.stopped = True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ctrl+shift+space", "<ctrl>+<shift>+<space>"),
        ("<ctrl>+<shift>+<space>", "<ctrl>+<shift>+<space>"),
        ("Super+Space", "<cmd>+<space>"),
        ("Meta+Control+K", "<cmd>+<ctrl>+k"),
        ("win+return", "<cmd>+<enter>"),
        ("windows+escape", "<cmd>+<esc>"),
        ("alt+k", "<alt>+k"),
        ("ctrl+ctrl+f", "<ctrl>+f"),
    ],
)
def test_normalize_hotkey_aliases_and_format(raw: str, expected: str) -> None:
    """Verify Linux key aliases and pynput hotkey formatting."""
    assert normalize_hotkey(raw) == expected


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        "   ",
        "ctrl+",
        "+space",
        "ctrl++k",
        "ctrl+shift",
        "space",
        "k",
        "ctrl+a+b",
        "<ctrl>+<>",
        "ctrl+<a<b",
    ],
)
def test_normalize_hotkey_invalid_inputs(invalid: str) -> None:
    """Verify malformed or incomplete hotkeys raise ValueError."""
    with pytest.raises(ValueError):
        normalize_hotkey(invalid)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ctrl+shift+space", "<Control><Shift>space"),
        ("<ctrl>+<shift>+<space>", "<Control><Shift>space"),
        ("super+space", "<Super>space"),
        ("meta+space", "<Super>space"),
        ("win+k", "<Super>k"),
        ("alt+k", "<Alt>k"),
        ("alt+enter", "<Alt>Return"),
        ("ctrl+return", "<Control>Return"),
        ("ctrl+escape", "<Control>Escape"),
        ("ctrl+shift+f12", "<Control><Shift>F12"),
    ],
)
def test_format_portal_trigger_valid(raw: str, expected: str) -> None:
    """Verify conversion to XDG Shortcuts Specification trigger syntax."""
    assert format_portal_trigger(raw) == expected


@pytest.mark.parametrize("invalid", ["", "   ", "ctrl+", "ctrl+shift", "space", "ctrl+a+b"])
def test_format_portal_trigger_invalid(invalid: str) -> None:
    """Verify invalid shortcuts raise ValueError when formatting portal triggers."""
    with pytest.raises(ValueError):
        format_portal_trigger(invalid)


@pytest.mark.parametrize(
    ("session_type", "wayland_display", "expected"),
    [
        ("wayland", None, True),
        ("WAYLAND", "", True),
        ("x11", "wayland-0", True),
        (None, "wayland-1", True),
        ("x11", None, False),
        ("x11", "", False),
        (None, None, False),
    ],
)
def test_is_wayland_session(
    monkeypatch: pytest.MonkeyPatch,
    *,
    session_type: str | None,
    wayland_display: str | None,
    expected: bool,
) -> None:
    """Verify Wayland session detection from XDG_SESSION_TYPE and WAYLAND_DISPLAY."""
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    if session_type is not None:
        monkeypatch.setenv("XDG_SESSION_TYPE", session_type)
    if wayland_display is not None:
        monkeypatch.setenv("WAYLAND_DISPLAY", wayland_display)

    assert is_wayland_session() is expected


def test_portal_listener_bus_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify PortalShortcutListener returns False and emits registration_failed without D-Bus."""
    fake_bus = _FakeDBusConnection(connected=False)
    monkeypatch.setattr(QDBusConnection, "sessionBus", staticmethod(lambda: fake_bus))

    listener = PortalShortcutListener("ctrl+shift+space")
    errors: list[str] = []
    listener.registration_failed.connect(errors.append)

    assert listener.start() is False
    assert len(errors) == 1
    assert "D-Bus" in errors[0]
    assert listener.last_error == errors[0]


def test_portal_listener_interface_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify PortalShortcutListener fails cleanly when GlobalShortcuts interface is absent."""
    fake_bus = _FakeDBusConnection(connected=True)
    fake_iface = _FakeDBusInterface(
        valid=True,
        error=_FakeDBusError(valid=True, message="Interface 'org.freedesktop.portal.GlobalShortcuts' was not found"),
    )
    monkeypatch.setattr(QDBusConnection, "sessionBus", staticmethod(lambda: fake_bus))
    monkeypatch.setattr(portal_module, "QDBusInterface", lambda *_args: fake_iface)

    listener = PortalShortcutListener("ctrl+shift+space")
    errors: list[str] = []
    listener.registration_failed.connect(errors.append)

    assert listener.start() is False
    assert len(errors) == 1
    assert "GlobalShortcuts" in errors[0]


def test_portal_listener_create_session_error_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify CreateSession D-Bus error cleans up subscription and emits registration_failed."""
    fake_bus = _FakeDBusConnection(connected=True)
    fake_iface = _FakeDBusInterface(
        valid=True,
        reply=_FakeDBusReply(
            QDBusMessage.MessageType.ErrorMessage,
            error_name="org.freedesktop.DBus.Error.AccessDenied",
            error_message="Portal denied session",
        ),
    )
    monkeypatch.setattr(QDBusConnection, "sessionBus", staticmethod(lambda: fake_bus))
    monkeypatch.setattr(portal_module, "QDBusInterface", lambda *_args: fake_iface)

    listener = PortalShortcutListener("ctrl+shift+space")
    errors: list[str] = []
    listener.registration_failed.connect(errors.append)

    assert listener.start() is False
    assert errors == ["Portal denied session"]
    assert len(fake_bus.connected_signals) == 1
    assert len(fake_bus.disconnected_signals) == 1


def test_portal_listener_create_bind_activate_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify CreateSession -> Response -> BindShortcuts -> Activated -> Session.Close flow."""
    fake_bus = _FakeDBusConnection(connected=True, base_service=":1.99")
    fake_iface = _FakeDBusInterface(valid=True)
    bind_calls: list[tuple[object, str, list[tuple[str, dict[str, str]]], str]] = []

    def _fake_send_bind(
        bus: object,
        session_handle: str,
        shortcuts: list[tuple[str, dict[str, str]]],
        handle_token: str,
    ) -> bool:
        bind_calls.append((bus, session_handle, shortcuts, handle_token))
        return True

    monkeypatch.setattr(QDBusConnection, "sessionBus", staticmethod(lambda: fake_bus))
    monkeypatch.setattr(portal_module, "QDBusInterface", lambda *_args: fake_iface)
    monkeypatch.setattr(portal_module, "_send_bind_shortcuts", _fake_send_bind)

    listener = PortalShortcutListener("super+space")
    triggered: list[bool] = []
    errors: list[str] = []
    listener.hotkey_triggered.connect(lambda: triggered.append(True))
    listener.registration_failed.connect(errors.append)

    assert listener.start() is True
    assert errors == []

    service, request_path, iface_name, sig_name, receiver, _slot = fake_bus.connected_signals[0]
    assert (service, iface_name, sig_name, receiver) == (PORTAL_SERVICE, REQUEST_INTERFACE, "Response", listener)
    assert isinstance(request_path, str)
    assert request_path.startswith("/org/freedesktop/portal/desktop/request/1_99/semsearch_")

    method_name, call_args = fake_iface.calls[0]
    assert method_name == "CreateSession"
    assert isinstance(call_args[0], dict)
    assert isinstance(call_args[0]["handle_token"], QDBusVariant)
    assert isinstance(call_args[0]["session_handle_token"], QDBusVariant)

    session_path = "/org/freedesktop/portal/desktop/session/1_99/semsearch_session_abc"
    listener._on_create_session_response(0, {"session_handle": session_path})

    signal_triples = [(entry[1], entry[2], entry[3]) for entry in fake_bus.connected_signals]
    assert (PORTAL_PATH, GLOBAL_SHORTCUTS_INTERFACE, "Activated") in signal_triples
    assert bind_calls == [
        (
            fake_bus,
            session_path,
            [(SHORTCUT_ID, {"description": SHORTCUT_DESCRIPTION, "preferred_trigger": "<Super>space"})],
            bind_calls[0][3],
        )
    ]

    listener._on_bind_shortcuts_response(0, {})
    listener._on_shortcut_activated(QDBusObjectPath(session_path), "other-shortcut", 100, {})
    listener._on_shortcut_activated(QDBusObjectPath("/other/session"), SHORTCUT_ID, 101, {})
    assert triggered == []

    listener._on_shortcut_activated(QDBusObjectPath(session_path), SHORTCUT_ID, 102, {})
    assert triggered == [True]

    listener.stop()
    disconnected_triples = [(entry[1], entry[2], entry[3]) for entry in fake_bus.disconnected_signals]
    assert (PORTAL_PATH, GLOBAL_SHORTCUTS_INTERFACE, "Activated") in disconnected_triples
    close_msg = fake_bus.sent_messages[0]
    assert (close_msg.service(), close_msg.path(), close_msg.interface(), close_msg.member()) == (
        PORTAL_SERVICE,
        session_path,
        SESSION_INTERFACE,
        "Close",
    )


def test_portal_listener_nonzero_responses_emit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify non-zero portal response codes emit registration_failed cleanly."""
    fake_bus = _FakeDBusConnection(connected=True)
    fake_iface = _FakeDBusInterface(valid=True)
    bind_called = False

    def _fake_send_bind(
        _bus: object,
        _session_handle: str,
        _shortcuts: list[tuple[str, dict[str, str]]],
        _handle_token: str,
    ) -> bool:
        nonlocal bind_called
        bind_called = True
        return True

    monkeypatch.setattr(QDBusConnection, "sessionBus", staticmethod(lambda: fake_bus))
    monkeypatch.setattr(portal_module, "QDBusInterface", lambda *_args: fake_iface)
    monkeypatch.setattr(portal_module, "_send_bind_shortcuts", _fake_send_bind)

    listener = PortalShortcutListener("ctrl+shift+space")
    errors: list[str] = []
    listener.registration_failed.connect(errors.append)

    assert listener.start() is True
    listener._on_create_session_response(1, {})
    assert bind_called is False
    assert len(errors) == 1
    assert "CreateSession" in errors[0]

    listener._on_bind_shortcuts_response(2, {})
    assert len(errors) == 2
    assert "BindShortcuts" in errors[1]


def test_hotkey_listener_wayland_selects_portal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify HotkeyListener uses PortalShortcutListener on Wayland and forwards trigger."""
    created_portals: list[_FakePortalShortcutListener] = []

    def _portal_factory(shortcut: str, parent: QObject | None = None) -> _FakePortalShortcutListener:
        portal = _FakePortalShortcutListener(shortcut, parent=parent, succeed=True)
        created_portals.append(portal)
        return portal

    monkeypatch.setattr(hotkey_module, "is_wayland_session", lambda: True)
    monkeypatch.setattr(hotkey_module, "PortalShortcutListener", _portal_factory)

    listener = HotkeyListener("super+space")
    statuses: list[str] = []
    triggered: list[bool] = []
    listener.status_changed.connect(statuses.append)
    listener.hotkey_triggered.connect(lambda: triggered.append(True))

    assert listener.start() is True
    assert listener.backend_name == "portal"
    assert listener.last_error == ""
    assert statuses == ["portal"]
    assert len(created_portals) == 1

    created_portals[0].hotkey_triggered.emit()
    assert triggered == [True]

    listener.stop()
    assert created_portals[0].stopped is True
    assert listener.backend_name == "none"
    assert statuses == ["portal", "none"]


def test_hotkey_listener_wayland_falls_back_to_pynput(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify HotkeyListener falls back to pynput when Wayland portal fails and DISPLAY is set."""
    fake_kb = _FakeKeyboardModule()
    monkeypatch.setattr(hotkey_module, "is_wayland_session", lambda: True)
    monkeypatch.setattr(
        hotkey_module,
        "PortalShortcutListener",
        lambda shortcut, parent=None: _FakePortalShortcutListener(
            shortcut, parent=parent, succeed=False, failure_reason="Portal absent"
        ),
    )
    monkeypatch.setattr(hotkey_module, "keyboard", fake_kb)
    monkeypatch.setenv("DISPLAY", ":0")

    listener = HotkeyListener("Super+Space")
    triggered: list[bool] = []
    listener.hotkey_triggered.connect(lambda: triggered.append(True))

    assert listener.start() is True
    assert listener.backend_name == "pynput"
    assert listener.last_error == ""
    assert len(fake_kb.instances) == 1
    assert "<cmd>+<space>" in fake_kb.instances[0].hotkeys
    assert fake_kb.instances[0].started is True

    fake_kb.instances[0].fire_first()
    assert triggered == [True]

    listener.stop()
    assert fake_kb.instances[0].stopped is True
    assert listener.backend_name == "none"


def test_hotkey_listener_wayland_no_portal_no_display_sets_last_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify HotkeyListener reports portal error cleanly when neither portal nor DISPLAY works."""
    monkeypatch.setattr(hotkey_module, "is_wayland_session", lambda: True)
    monkeypatch.setattr(
        hotkey_module,
        "PortalShortcutListener",
        lambda shortcut, parent=None: _FakePortalShortcutListener(
            shortcut, parent=parent, succeed=False, failure_reason="GlobalShortcuts unavailable"
        ),
    )
    monkeypatch.delenv("DISPLAY", raising=False)

    listener = HotkeyListener("ctrl+shift+space")
    assert listener.start() is False
    assert listener.backend_name == "none"
    assert "GlobalShortcuts unavailable" in listener.last_error


def test_hotkey_listener_x11_selects_pynput_and_emits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify HotkeyListener uses pynput directly on X11 sessions without touching portal."""
    fake_kb = _FakeKeyboardModule()
    portal_called = False

    def _unexpected_portal(*_args: object, **_kwargs: object) -> None:
        nonlocal portal_called
        portal_called = True

    monkeypatch.setattr(hotkey_module, "is_wayland_session", lambda: False)
    monkeypatch.setattr(hotkey_module, "PortalShortcutListener", _unexpected_portal)
    monkeypatch.setattr(hotkey_module, "keyboard", fake_kb)
    monkeypatch.setenv("DISPLAY", ":1")

    listener = HotkeyListener("Meta+Control+K")
    statuses: list[str] = []
    triggered: list[bool] = []
    listener.status_changed.connect(statuses.append)
    listener.hotkey_triggered.connect(lambda: triggered.append(True))

    assert listener.start() is True
    assert portal_called is False
    assert listener.backend_name == "pynput"
    assert statuses == ["pynput"]
    assert "<cmd>+<ctrl>+k" in fake_kb.instances[0].hotkeys

    fake_kb.instances[0].fire_first()
    assert triggered == [True]

    listener.stop()
    assert fake_kb.instances[0].stopped is True
    assert listener.backend_name == "none"


def test_hotkey_listener_pynput_failure_sets_last_error_without_crashing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify native pynput errors and malformed hotkeys set last_error and return False."""
    monkeypatch.setattr(hotkey_module, "is_wayland_session", lambda: False)
    monkeypatch.setenv("DISPLAY", ":0")

    failing_kb = _FakeKeyboardModule(error=OSError("X11 connection refused"))
    monkeypatch.setattr(hotkey_module, "keyboard", failing_kb)
    listener = HotkeyListener("ctrl+shift+space")
    assert listener.start() is False
    assert listener.backend_name == "none"
    assert "X11 connection refused" in listener.last_error

    ok_kb = _FakeKeyboardModule()
    monkeypatch.setattr(hotkey_module, "keyboard", ok_kb)
    bad_listener = HotkeyListener("invalid_only_key")
    assert bad_listener.start() is False
    assert bad_listener.backend_name == "none"
    assert "modifier" in bad_listener.last_error


def test_hotkey_listener_portal_async_failure_updates_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify asynchronous portal registration failure resets backend_name and exposes last_error."""
    created_portals: list[_FakePortalShortcutListener] = []

    def _portal_factory(shortcut: str, parent: QObject | None = None) -> _FakePortalShortcutListener:
        portal = _FakePortalShortcutListener(shortcut, parent=parent, succeed=True)
        created_portals.append(portal)
        return portal

    monkeypatch.setattr(hotkey_module, "is_wayland_session", lambda: True)
    monkeypatch.setattr(hotkey_module, "PortalShortcutListener", _portal_factory)

    listener = HotkeyListener("ctrl+shift+space")
    statuses: list[str] = []
    listener.status_changed.connect(statuses.append)

    assert listener.start() is True
    assert listener.backend_name == "portal"

    created_portals[0].registration_failed.emit("User cancelled shortcut dialog")
    assert listener.backend_name == "none"
    assert listener.last_error == "User cancelled shortcut dialog"
    assert statuses == ["portal", "none"]
    assert created_portals[0].stopped is True


def test_semsearch_svg_icon_exists_valid_xml_and_renders(qapp: QApplication) -> None:
    """Verify semsearch.svg exists, is valid 64x64 SVG XML with brand palette, and renders in Qt."""
    assert qapp is not None
    svg_path = Path(__file__).resolve().parent.parent / "src" / "semsearch" / "gui" / "icons" / "semsearch.svg"
    assert svg_path.is_file()

    xml_text = svg_path.read_text(encoding="utf-8")
    reader = QXmlStreamReader(xml_text)
    root_name = ""
    root_attrs: dict[str, str] = {}
    while not reader.atEnd():
        token = reader.readNext()
        if token == QXmlStreamReader.TokenType.StartElement and not root_name:
            root_name = reader.name()
            attrs = reader.attributes()
            root_attrs = {
                "width": attrs.value("width"),
                "height": attrs.value("height"),
                "viewBox": attrs.value("viewBox"),
            }
    assert not reader.hasError(), reader.errorString()
    assert root_name == "svg"
    assert root_attrs == {"width": "64", "height": "64", "viewBox": "0 0 64 64"}

    for color in ("#6366F1", "#8B5CF6", "#0F1115"):
        assert color in xml_text

    renderer = QSvgRenderer(str(svg_path))
    assert renderer.isValid()

    icon = QIcon(str(svg_path))
    assert not icon.isNull()
    pixmap = icon.pixmap(QSize(64, 64))
    assert not pixmap.isNull()
    assert pixmap.width() == 64
    assert pixmap.height() == 64
