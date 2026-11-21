"""Unit tests for KRunner and GNOME Shell native D-Bus search providers."""

from collections.abc import Generator
from typing import cast

import pytest
from PySide6.QtCore import QXmlStreamReader
from PySide6.QtDBus import QDBusConnection, QDBusMessage, QDBusVirtualObject
from PySide6.QtWidgets import QApplication

import semsearch.gui.desktop_providers as providers_module
from semsearch.config import AppConfig
from semsearch.core.database import Database
from semsearch.core.types import Candidate, FileKind, FileState, Modality, SearchHit, SegmentKind
from semsearch.gui.desktop_providers import (
    DBUS_SERVICE_NAME,
    GNOME_INTERFACE,
    GNOME_OBJECT_PATH,
    KRUNNER_INTERFACE,
    KRUNNER_MATCH_TYPE,
    KRUNNER_OBJECT_PATH,
    DesktopSearchProviders,
    GnomeSearchProvider,
    KRunnerProvider,
)
from semsearch.services.searcher import SearchService


class _FakeDBusConnection:
    """In-memory stand-in for QDBusConnection recording sent messages and registrations."""

    def __init__(
        self,
        *,
        connected: bool = True,
        register_service_ok: bool = True,
        fail_virtual_path: str = "",
    ) -> None:
        self._connected = connected
        self._register_service_ok = register_service_ok
        self._fail_virtual_path = fail_virtual_path
        self.sent_messages: list[QDBusMessage] = []
        self.registered_services: list[str] = []
        self.unregistered_services: list[str] = []
        self.registered_objects: dict[str, QDBusVirtualObject] = {}
        self.unregistered_objects: list[str] = []

    def isConnected(self) -> bool:  # noqa: N802
        return self._connected

    def registerService(self, service_name: str) -> bool:  # noqa: N802
        if not self._register_service_ok:
            return False
        self.registered_services.append(service_name)
        return True

    def unregisterService(self, service_name: str) -> bool:  # noqa: N802
        self.unregistered_services.append(service_name)
        return True

    def registerVirtualObject(self, path: str, obj: QDBusVirtualObject) -> bool:  # noqa: N802
        if path == self._fail_virtual_path:
            return False
        self.registered_objects[path] = obj
        return True

    def unregisterObject(self, path: str) -> None:  # noqa: N802
        self.unregistered_objects.append(path)
        self.registered_objects.pop(path, None)

    def send(self, message: QDBusMessage) -> bool:
        self.sent_messages.append(message)
        return True


def _parse_dbus_interface_xml(xml_text: str) -> tuple[str, dict[str, list[tuple[str, str]]]]:
    """Parse D-Bus introspection XML via Qt's QXmlStreamReader and extract interface and method args."""
    reader = QXmlStreamReader(xml_text)
    interface_name = ""
    methods: dict[str, list[tuple[str, str]]] = {}
    current_method = ""

    while not reader.atEnd():
        token = reader.readNext()
        if token == QXmlStreamReader.TokenType.StartElement:
            tag = reader.name()
            attrs = reader.attributes()
            if tag == "interface":
                interface_name = attrs.value("name")
            elif tag == "method":
                current_method = attrs.value("name")
                methods[current_method] = []
            elif tag == "arg" and current_method:
                methods[current_method].append((attrs.value("direction"), attrs.value("type")))
        elif token == QXmlStreamReader.TokenType.EndElement and reader.name() == "method":
            current_method = ""

    assert not reader.hasError(), reader.errorString()
    return interface_name, methods


@pytest.fixture
def seeded_search_service() -> Generator[tuple[SearchService, AppConfig], None, None]:
    """Provide a real SearchService over an in-memory database with multimodal sample hits."""
    db = Database(":memory:")
    pdf_id = db.insert_file(Candidate("/docs/quarterly_report.pdf", FileKind.PDF, 4096, 1700000100.0, 1, 1))
    db.insert_segment(
        pdf_id,
        SegmentKind.PAGE,
        Modality.TEXT,
        loc=2.0,
        excerpt="Quarterly financial summary for stakeholders",
        body="Quarterly financial summary for stakeholders",
    )
    db.update_file_state(pdf_id, FileState.DONE)

    img_id = db.insert_file(Candidate("/images/quarterly_chart.png", FileKind.IMAGE, 2048, 1700000050.0, 2, 1))
    db.insert_segment(
        img_id,
        SegmentKind.IMAGE,
        Modality.IMAGE,
        excerpt="Quarterly revenue bar chart",
        body="Quarterly revenue bar chart",
    )
    db.update_file_state(img_id, FileState.DONE)

    config = AppConfig(code_editor="code")
    service = SearchService(db, ollama=None)
    try:
        yield service, config
    finally:
        db.close()


def test_krunner_and_gnome_introspection_xml_are_valid(
    qapp: QApplication,
    seeded_search_service: tuple[SearchService, AppConfig],
) -> None:
    del qapp
    service, config = seeded_search_service
    krunner = KRunnerProvider(service, config)
    gnome = GnomeSearchProvider(service, config)

    krunner_iface, krunner_methods = _parse_dbus_interface_xml(krunner.introspect(KRUNNER_OBJECT_PATH))
    assert krunner_iface == KRUNNER_INTERFACE
    assert krunner_methods == {
        "Actions": [("out", "a(sss)")],
        "Match": [("in", "s"), ("out", "a(sssida{sv})")],
        "Run": [("in", "s"), ("in", "s")],
    }

    gnome_iface, gnome_methods = _parse_dbus_interface_xml(gnome.introspect(GNOME_OBJECT_PATH))
    assert gnome_iface == GNOME_INTERFACE
    assert gnome_methods == {
        "GetInitialResultSet": [("in", "as"), ("out", "as")],
        "GetSubsearchResultSet": [("in", "as"), ("in", "as"), ("out", "as")],
        "GetResultMetas": [("in", "as"), ("out", "aa{sv}")],
        "ActivateResult": [("in", "s"), ("in", "as"), ("in", "u")],
        "LaunchSearch": [("in", "as"), ("in", "u")],
    }


def test_krunner_actions_match_and_run(
    qapp: QApplication,
    seeded_search_service: tuple[SearchService, AppConfig],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del qapp
    service, config = seeded_search_service
    provider = KRunnerProvider(service, config)

    assert provider.actions_payload() == [("reveal", "Show in Files", "document-open-folder")]
    assert provider.match_query("   ") == []

    matches = provider.match_query("quart")
    assert len(matches) == 2

    by_id = {m[0]: m for m in matches}
    pdf_match = by_id["/docs/quarterly_report.pdf"]
    assert pdf_match[1] == "quarterly_report.pdf"
    assert pdf_match[2] == "application-pdf"
    assert pdf_match[3] == KRUNNER_MATCH_TYPE
    assert 0.0 < pdf_match[4] <= 1.0
    assert pdf_match[5]["category"] == "PDF Documents"
    assert "/docs" in str(pdf_match[5]["subtext"])

    img_match = by_id["/images/quarterly_chart.png"]
    assert img_match[2] == "image-x-generic"
    assert img_match[5]["category"] == "Pictures"
    assert max(m[4] for m in matches) == 1.0

    opened_hits: list[tuple[SearchHit, AppConfig | None]] = []
    revealed_paths: list[str] = []

    def fake_execute(hit: SearchHit, cfg: AppConfig | None = None) -> bool:
        opened_hits.append((hit, cfg))
        return True

    def fake_reveal(path: str) -> bool:
        revealed_paths.append(path)
        return True

    monkeypatch.setattr(providers_module, "execute_hit_action", fake_execute)
    monkeypatch.setattr(providers_module, "reveal_in_file_manager", fake_reveal)

    assert provider.run_match("/docs/quarterly_report.pdf", "") is True
    assert len(opened_hits) == 1
    assert opened_hits[0][0].path == "/docs/quarterly_report.pdf"
    assert opened_hits[0][1] is config

    assert provider.run_match("/docs/quarterly_report.pdf", "reveal") is True
    assert revealed_paths == ["/docs/quarterly_report.pdf"]

    assert provider.run_match("/nonexistent/file.pdf", "") is False


def test_krunner_handle_message_dispatches_all_methods(
    qapp: QApplication,
    seeded_search_service: tuple[SearchService, AppConfig],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del qapp
    service, config = seeded_search_service
    provider = KRunnerProvider(service, config)
    conn = _FakeDBusConnection()
    dbus_conn = cast(QDBusConnection, conn)

    revealed: list[str] = []

    def fake_reveal(path: str) -> bool:
        revealed.append(path)
        return True

    monkeypatch.setattr(providers_module, "reveal_in_file_manager", fake_reveal)

    introspect_msg = QDBusMessage.createMethodCall(
        DBUS_SERVICE_NAME,
        KRUNNER_OBJECT_PATH,
        "org.freedesktop.DBus.Introspectable",
        "Introspect",
    )
    assert provider.handleMessage(introspect_msg, dbus_conn) is False

    actions_msg = QDBusMessage.createMethodCall(DBUS_SERVICE_NAME, KRUNNER_OBJECT_PATH, KRUNNER_INTERFACE, "Actions")
    assert provider.handleMessage(actions_msg, dbus_conn) is True

    match_msg = QDBusMessage.createMethodCall(DBUS_SERVICE_NAME, KRUNNER_OBJECT_PATH, KRUNNER_INTERFACE, "Match")
    match_msg.setArguments(["quarterly"])
    assert provider.handleMessage(match_msg, dbus_conn) is True

    run_msg = QDBusMessage.createMethodCall(DBUS_SERVICE_NAME, KRUNNER_OBJECT_PATH, KRUNNER_INTERFACE, "Run")
    run_msg.setArguments(["/docs/quarterly_report.pdf", "reveal"])
    assert provider.handleMessage(run_msg, dbus_conn) is True
    assert revealed == ["/docs/quarterly_report.pdf"]

    unknown_msg = QDBusMessage.createMethodCall(DBUS_SERVICE_NAME, KRUNNER_OBJECT_PATH, KRUNNER_INTERFACE, "Unknown")
    assert provider.handleMessage(unknown_msg, dbus_conn) is False

    assert len(conn.sent_messages) == 3
    assert all(msg.type() == QDBusMessage.MessageType.ReplyMessage for msg in conn.sent_messages)


def test_gnome_search_provider_methods_and_launch_signal(
    qapp: QApplication,
    seeded_search_service: tuple[SearchService, AppConfig],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del qapp
    service, config = seeded_search_service
    provider = GnomeSearchProvider(service, config)

    assert provider.get_result_ids(["", "   "]) == []

    ids = provider.get_result_ids(["financial", "stakeholders"])
    assert ids == ["/docs/quarterly_report.pdf"]

    metas = provider.get_result_metas(["/docs/quarterly_report.pdf", "/missing.pdf"])
    assert len(metas) == 1
    assert metas[0]["id"] == "/docs/quarterly_report.pdf"
    assert metas[0]["name"] == "quarterly_report.pdf"
    assert metas[0]["gicon"] == "application-pdf"
    assert "/docs" in str(metas[0]["description"])
    assert "financial" in str(metas[0]["description"]).lower()

    activated: list[str] = []

    def fake_execute(hit: SearchHit, cfg: AppConfig | None = None) -> bool:
        del cfg
        activated.append(hit.path)
        return True

    monkeypatch.setattr(providers_module, "execute_hit_action", fake_execute)
    assert provider.activate_result("/docs/quarterly_report.pdf") is True
    assert activated == ["/docs/quarterly_report.pdf"]
    assert provider.activate_result("/missing.pdf") is False

    launched_queries: list[str] = []
    provider.launch_panel_requested.connect(launched_queries.append)
    provider.launch_search(["quarterly", "summary"])
    assert launched_queries == ["quarterly summary"]


def test_gnome_handle_message_dispatches_all_five_methods(
    qapp: QApplication,
    seeded_search_service: tuple[SearchService, AppConfig],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del qapp
    service, config = seeded_search_service
    provider = GnomeSearchProvider(service, config)
    conn = _FakeDBusConnection()
    dbus_conn = cast(QDBusConnection, conn)

    activated: list[str] = []
    launched: list[str] = []
    provider.launch_panel_requested.connect(launched.append)

    def fake_execute(hit: SearchHit, cfg: AppConfig | None = None) -> bool:
        del cfg
        activated.append(hit.path)
        return True

    monkeypatch.setattr(providers_module, "execute_hit_action", fake_execute)

    init_msg = QDBusMessage.createMethodCall(
        DBUS_SERVICE_NAME, GNOME_OBJECT_PATH, GNOME_INTERFACE, "GetInitialResultSet"
    )
    init_msg.setArguments([["quarterly"]])
    assert provider.handleMessage(init_msg, dbus_conn) is True

    sub_msg = QDBusMessage.createMethodCall(
        DBUS_SERVICE_NAME, GNOME_OBJECT_PATH, GNOME_INTERFACE, "GetSubsearchResultSet"
    )
    sub_msg.setArguments([["/docs/quarterly_report.pdf"], ["financial", "stakeholders"]])
    assert provider.handleMessage(sub_msg, dbus_conn) is True

    metas_msg = QDBusMessage.createMethodCall(DBUS_SERVICE_NAME, GNOME_OBJECT_PATH, GNOME_INTERFACE, "GetResultMetas")
    metas_msg.setArguments([["/docs/quarterly_report.pdf"]])
    assert provider.handleMessage(metas_msg, dbus_conn) is True

    act_msg = QDBusMessage.createMethodCall(DBUS_SERVICE_NAME, GNOME_OBJECT_PATH, GNOME_INTERFACE, "ActivateResult")
    act_msg.setArguments(["/docs/quarterly_report.pdf", ["financial"], 999])
    assert provider.handleMessage(act_msg, dbus_conn) is True
    assert activated == ["/docs/quarterly_report.pdf"]

    launch_msg = QDBusMessage.createMethodCall(DBUS_SERVICE_NAME, GNOME_OBJECT_PATH, GNOME_INTERFACE, "LaunchSearch")
    launch_msg.setArguments([["quarterly", "budget"], 1000])
    assert provider.handleMessage(launch_msg, dbus_conn) is True
    assert launched == ["quarterly budget"]

    other_iface_msg = QDBusMessage.createMethodCall(
        DBUS_SERVICE_NAME,
        GNOME_OBJECT_PATH,
        "org.freedesktop.DBus.Peer",
        "Ping",
    )
    assert provider.handleMessage(other_iface_msg, dbus_conn) is False

    assert len(conn.sent_messages) == 5
    assert all(msg.type() == QDBusMessage.MessageType.ReplyMessage for msg in conn.sent_messages)


def test_desktop_search_providers_register_unregister_and_signal_forwarding(
    qapp: QApplication,
    seeded_search_service: tuple[SearchService, AppConfig],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del qapp
    service, config = seeded_search_service

    disconnected_bus = _FakeDBusConnection(connected=False)
    monkeypatch.setattr(QDBusConnection, "sessionBus", staticmethod(lambda: disconnected_bus))
    providers = DesktopSearchProviders(service, config)
    assert providers.register() is False
    assert disconnected_bus.registered_services == []

    bus = _FakeDBusConnection(connected=True)
    monkeypatch.setattr(QDBusConnection, "sessionBus", staticmethod(lambda: bus))
    assert providers.register() is True
    assert providers.register() is True  # Idempotent when already registered.
    assert bus.registered_services == [DBUS_SERVICE_NAME]
    assert set(bus.registered_objects) == {KRUNNER_OBJECT_PATH, GNOME_OBJECT_PATH}

    forwarded: list[str] = []
    providers.launch_panel_requested.connect(forwarded.append)
    providers.gnome.launch_search(["budget", "report"])
    assert forwarded == ["budget report"]

    providers.unregister()
    assert set(bus.unregistered_objects) == {KRUNNER_OBJECT_PATH, GNOME_OBJECT_PATH}
    assert bus.unregistered_services == [DBUS_SERVICE_NAME]

    providers.unregister()  # Idempotent when already unregistered.
    assert len(bus.unregistered_services) == 1


def test_desktop_search_providers_rollback_on_partial_registration_failure(
    qapp: QApplication,
    seeded_search_service: tuple[SearchService, AppConfig],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del qapp
    service, config = seeded_search_service

    partial_bus = _FakeDBusConnection(connected=True, fail_virtual_path=GNOME_OBJECT_PATH)
    monkeypatch.setattr(QDBusConnection, "sessionBus", staticmethod(lambda: partial_bus))

    providers = DesktopSearchProviders(service, config)
    assert providers.register() is False
    assert partial_bus.unregistered_objects == [KRUNNER_OBJECT_PATH]
    assert partial_bus.unregistered_services == [DBUS_SERVICE_NAME]
