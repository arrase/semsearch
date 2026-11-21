"""Native Linux desktop search providers over D-Bus for KDE KRunner and GNOME Shell."""

from pathlib import Path

from PySide6.QtCore import QMetaType, QObject, Signal
from PySide6.QtDBus import (
    QDBusArgument,
    QDBusConnection,
    QDBusMessage,
    QDBusVariant,
    QDBusVirtualObject,
)

from semsearch.config import AppConfig
from semsearch.core.types import FileKind, SearchHit
from semsearch.i18n import tr
from semsearch.services.actions import execute_hit_action, reveal_in_file_manager
from semsearch.services.searcher import SearchService

DBUS_SERVICE_NAME = "org.semsearch.DesktopSearch"
KRUNNER_OBJECT_PATH = "/krunner"
GNOME_OBJECT_PATH = "/org/semsearch/SearchProvider"

KRUNNER_INTERFACE = "org.kde.krunner1"
GNOME_INTERFACE = "org.gnome.Shell.SearchProvider2"

KRUNNER_MATCH_TYPE = 100
_MAX_PROVIDER_RESULTS = 10
_MAX_SUBTEXT_EXCERPT_LEN = 120

_KIND_ICONS: dict[FileKind, str] = {
    FileKind.PDF: "application-pdf",
    FileKind.DOC: "x-office-document",
    FileKind.IMAGE: "image-x-generic",
    FileKind.SCREENSHOT: "image-x-generic",
    FileKind.AUDIO: "audio-x-generic",
    FileKind.VIDEO: "video-x-generic",
    FileKind.CODE: "text-x-script",
}

_KIND_CATEGORIES: dict[FileKind, str] = {
    FileKind.PDF: "PDF Documents",
    FileKind.DOC: "Documents",
    FileKind.IMAGE: "Pictures",
    FileKind.SCREENSHOT: "Screenshots",
    FileKind.AUDIO: "Audio",
    FileKind.VIDEO: "Media",
    FileKind.CODE: "Code",
}

KRUNNER_INTROSPECTION_XML = """  <interface name="org.kde.krunner1">
    <method name="Actions">
      <arg name="matches" type="a(sss)" direction="out"/>
    </method>
    <method name="Match">
      <arg name="query" type="s" direction="in"/>
      <arg name="matches" type="a(sssida{sv})" direction="out"/>
    </method>
    <method name="Run">
      <arg name="matchId" type="s" direction="in"/>
      <arg name="actionId" type="s" direction="in"/>
    </method>
  </interface>"""

GNOME_INTROSPECTION_XML = """  <interface name="org.gnome.Shell.SearchProvider2">
    <method name="GetInitialResultSet">
      <arg name="terms" type="as" direction="in"/>
      <arg name="results" type="as" direction="out"/>
    </method>
    <method name="GetSubsearchResultSet">
      <arg name="previous_results" type="as" direction="in"/>
      <arg name="terms" type="as" direction="in"/>
      <arg name="results" type="as" direction="out"/>
    </method>
    <method name="GetResultMetas">
      <arg name="identifiers" type="as" direction="in"/>
      <arg name="metas" type="aa{sv}" direction="out"/>
    </method>
    <method name="ActivateResult">
      <arg name="identifier" type="s" direction="in"/>
      <arg name="terms" type="as" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="LaunchSearch">
      <arg name="terms" type="as" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
  </interface>"""


def _format_hit_subtext(hit: SearchHit) -> str:
    parent = Path(hit.path).parent
    home = Path.home()
    if parent == home:
        folder = "~"
    elif parent.is_relative_to(home):
        folder = f"~/{parent.relative_to(home)}"
    else:
        folder = str(parent)
    excerpt = " ".join(hit.excerpt.split())
    if not excerpt:
        return folder
    if len(excerpt) > _MAX_SUBTEXT_EXCERPT_LEN:
        excerpt = f"{excerpt[: _MAX_SUBTEXT_EXCERPT_LEN - 1]}…"
    return f"{folder} — {excerpt}"


def _as_str_list(raw: object) -> list[str]:
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw]
    return []


def _write_variant_map(arg: QDBusArgument, mapping: dict[str, object]) -> None:
    mt_str = QMetaType(QMetaType.Type.QString)
    mt_var = QMetaType.fromName(b"QDBusVariant")
    arg.beginMap(mt_str, mt_var)
    for key, value in mapping.items():
        arg.beginMapEntry()
        arg.appendVariant(key)
        arg.appendVariant(QDBusVariant(value))
        arg.endMapEntry()
    arg.endMap()


def _encode_string_list(values: list[str]) -> QDBusArgument:
    arg = QDBusArgument()
    arg.beginArray(QMetaType(QMetaType.Type.QString))
    for value in values:
        arg.appendVariant(value)
    arg.endArray()
    return arg


def _encode_variant_map_list(items: list[dict[str, object]]) -> QDBusArgument:
    arg = QDBusArgument()
    arg.beginArray(QMetaType(QMetaType.Type.QVariantMap))
    for mapping in items:
        _write_variant_map(arg, mapping)
    arg.endArray()
    return arg


def _encode_krunner_action(action: tuple[str, str, str]) -> QDBusArgument:
    action_id, text, icon_name = action
    arg = QDBusArgument()
    arg.beginStructure()
    arg.appendVariant(action_id)
    arg.appendVariant(text)
    arg.appendVariant(icon_name)
    arg.endStructure()
    return arg


def _encode_krunner_match(match: tuple[str, str, str, int, float, dict[str, object]]) -> QDBusArgument:
    match_id, text, icon_name, match_type, relevance, properties = match
    arg = QDBusArgument()
    arg.beginStructure()
    arg.appendVariant(match_id)
    arg.appendVariant(text)
    arg.appendVariant(icon_name)
    arg.appendVariant(match_type)
    arg.appendVariant(relevance)
    _write_variant_map(arg, properties)
    arg.endStructure()
    return arg


class KRunnerProvider(QDBusVirtualObject):
    """D-Bus virtual object implementing org.kde.krunner1 for KDE Plasma KRunner."""

    def __init__(
        self,
        search_service: SearchService,
        config: AppConfig,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._search_service = search_service
        self._config = config
        self._hits: dict[str, SearchHit] = {}

    def introspect(self, path: str) -> str:
        """Return D-Bus introspection XML for org.kde.krunner1."""
        del path
        return KRUNNER_INTROSPECTION_XML

    def actions_payload(self) -> list[tuple[str, str, str]]:
        """Return secondary KRunner actions exposed for SemSearch results."""
        return [("reveal", tr("Show in Files"), "document-open-folder")]

    def match_query(self, query: str) -> list[tuple[str, str, str, int, float, dict[str, object]]]:
        """Execute search query and return KRunner remote match tuples."""
        cleaned = query.strip()
        if not cleaned:
            self._hits.clear()
            return []

        results = self._search_service.search(cleaned, limit=_MAX_PROVIDER_RESULTS, prefix_last=True)
        hits = results.ranked
        self._hits = {hit.path: hit for hit in hits}
        max_score = max((hit.score for hit in hits), default=1.0)
        denom = max_score if max_score > 0.0 else 1.0

        matches: list[tuple[str, str, str, int, float, dict[str, object]]] = []
        for hit in hits:
            relevance = min(max(hit.score / denom, 0.0), 1.0)
            properties: dict[str, object] = {
                "subtext": _format_hit_subtext(hit),
                "category": tr(_KIND_CATEGORIES[hit.kind]),
            }
            matches.append(
                (
                    hit.path,
                    Path(hit.path).name,
                    _KIND_ICONS[hit.kind],
                    KRUNNER_MATCH_TYPE,
                    relevance,
                    properties,
                )
            )
        return matches

    def run_match(self, match_id: str, action_id: str) -> bool:
        """Execute default open action or secondary reveal action for a cached match."""
        hit = self._hits.get(match_id)
        if hit is None:
            return False
        if action_id == "reveal":
            return reveal_in_file_manager(hit.path)
        return execute_hit_action(hit, self._config)

    def handleMessage(self, message: QDBusMessage, connection: QDBusConnection) -> bool:
        """Dispatch incoming org.kde.krunner1 D-Bus method calls."""
        if message.interface() not in ("", KRUNNER_INTERFACE):
            return False

        member = message.member()
        args = message.arguments()
        reply = message.createReply()
        if member == "Actions":
            reply.setArguments([[_encode_krunner_action(act) for act in self.actions_payload()]])
        elif member == "Match":
            query = str(args[0]) if args else ""
            reply.setArguments([[_encode_krunner_match(match) for match in self.match_query(query)]])
        elif member == "Run":
            match_id = str(args[0]) if args else ""
            action_id = str(args[1]) if len(args) > 1 else ""
            self.run_match(match_id, action_id)
        else:
            return False

        connection.send(reply)
        return True


class GnomeSearchProvider(QDBusVirtualObject):
    """D-Bus virtual object implementing org.gnome.Shell.SearchProvider2."""

    launch_panel_requested = Signal(str)

    def __init__(
        self,
        search_service: SearchService,
        config: AppConfig,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._search_service = search_service
        self._config = config
        self._hits: dict[str, SearchHit] = {}

    def introspect(self, path: str) -> str:
        """Return D-Bus introspection XML for org.gnome.Shell.SearchProvider2."""
        del path
        return GNOME_INTROSPECTION_XML

    def get_result_ids(self, terms: list[str]) -> list[str]:
        """Run search for GNOME query terms and return cached result IDs."""
        query = " ".join(term.strip() for term in terms if term.strip())
        if not query:
            self._hits.clear()
            return []

        results = self._search_service.search(query, limit=_MAX_PROVIDER_RESULTS, prefix_last=True)
        hits = results.ranked
        self._hits = {hit.path: hit for hit in hits}
        return [hit.path for hit in hits]

    def get_result_metas(self, ids: list[str]) -> list[dict[str, object]]:
        """Build GNOME SearchProvider2 metadata dictionaries for cached result IDs."""
        metas: list[dict[str, object]] = []
        for result_id in ids:
            hit = self._hits.get(result_id)
            if hit is None:
                continue
            metas.append(
                {
                    "id": hit.path,
                    "name": Path(hit.path).name,
                    "gicon": _KIND_ICONS[hit.kind],
                    "description": _format_hit_subtext(hit),
                }
            )
        return metas

    def activate_result(self, result_id: str) -> bool:
        """Open a cached GNOME search result with its deep-link desktop action."""
        hit = self._hits.get(result_id)
        if hit is None:
            return False
        return execute_hit_action(hit, self._config)

    def launch_search(self, terms: list[str]) -> None:
        """Emit signal requesting the SemSearch quick panel pre-filled with terms."""
        query = " ".join(term.strip() for term in terms if term.strip())
        self.launch_panel_requested.emit(query)

    def handleMessage(self, message: QDBusMessage, connection: QDBusConnection) -> bool:
        """Dispatch incoming org.gnome.Shell.SearchProvider2 D-Bus method calls."""
        if message.interface() not in ("", GNOME_INTERFACE):
            return False

        member = message.member()
        args = message.arguments()
        reply = message.createReply()
        if member == "GetInitialResultSet":
            ids = self.get_result_ids(_as_str_list(args[0]) if args else [])
            reply.setArguments([_encode_string_list(ids)])
        elif member == "GetSubsearchResultSet":
            ids = self.get_result_ids(_as_str_list(args[1]) if len(args) > 1 else [])
            reply.setArguments([_encode_string_list(ids)])
        elif member == "GetResultMetas":
            metas = self.get_result_metas(_as_str_list(args[0]) if args else [])
            reply.setArguments([_encode_variant_map_list(metas)])
        elif member == "ActivateResult":
            self.activate_result(str(args[0]) if args else "")
        elif member == "LaunchSearch":
            self.launch_search(_as_str_list(args[0]) if args else [])
        else:
            return False

        connection.send(reply)
        return True


class DesktopSearchProviders(QObject):
    """Registers and manages SemSearch D-Bus search providers for KRunner and GNOME Shell."""

    launch_panel_requested = Signal(str)

    def __init__(
        self,
        search_service: SearchService,
        config: AppConfig,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.krunner = KRunnerProvider(search_service, config, parent=self)
        self.gnome = GnomeSearchProvider(search_service, config, parent=self)
        self.gnome.launch_panel_requested.connect(self.launch_panel_requested)
        self._bus: QDBusConnection | None = None
        self._registered = False

    def register(self) -> bool:
        """Register the D-Bus service name and both search provider virtual objects."""
        if self._registered:
            return True

        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return False
        if not bus.registerService(DBUS_SERVICE_NAME):
            return False

        krunner_ok = bus.registerVirtualObject(KRUNNER_OBJECT_PATH, self.krunner)
        gnome_ok = bus.registerVirtualObject(GNOME_OBJECT_PATH, self.gnome)
        if not (krunner_ok and gnome_ok):
            if krunner_ok:
                bus.unregisterObject(KRUNNER_OBJECT_PATH)
            if gnome_ok:
                bus.unregisterObject(GNOME_OBJECT_PATH)
            bus.unregisterService(DBUS_SERVICE_NAME)
            return False

        self._bus = bus
        self._registered = True
        return True

    def unregister(self) -> None:
        """Unregister both search provider objects and release the D-Bus service name."""
        if not self._registered or self._bus is None:
            return
        self._bus.unregisterObject(KRUNNER_OBJECT_PATH)
        self._bus.unregisterObject(GNOME_OBJECT_PATH)
        self._bus.unregisterService(DBUS_SERVICE_NAME)
        self._bus = None
        self._registered = False
