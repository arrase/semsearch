"""End-to-end desktop deep-linking action dispatch tests."""

import subprocess
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtDBus import QDBusMessage

from semsearch.config import AppConfig
from semsearch.core.types import FileKind, SearchHit
from semsearch.services.actions import (
    execute_hit_action,
    reveal_in_file_manager,
)


def test_code_editor_actions_deep_link(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[list[str]] = []

    def mock_popen(args: list[str], **_kwargs: Any) -> None:
        launched.append(args)

    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    hit_code = SearchHit(path="/src/app.py", kind=FileKind.CODE, score=1.0, focus=42.0)

    # 1. VS Code (`code -g file:line`)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "code" else None)
    assert execute_hit_action(hit_code) is True
    assert launched[-1] == ["code", "-g", "/src/app.py:42"]

    # 2. Cursor (`cursor -g file:line`)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "cursor" else None)
    assert execute_hit_action(hit_code) is True
    assert launched[-1] == ["cursor", "-g", "/src/app.py:42"]

    # 3. Zed (`zed file:line`)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "zed" else None)
    assert execute_hit_action(hit_code) is True
    assert launched[-1] == ["zed", "/src/app.py:42"]

    # 4. Sublime Text (`subl file:line`)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "subl" else None)
    assert execute_hit_action(hit_code) is True
    assert launched[-1] == ["subl", "/src/app.py:42"]

    # 5. Explicit JetBrains PyCharm (`pycharm --line line file`)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "pycharm" else None)
    cfg_pycharm = AppConfig(code_editor="pycharm")
    assert execute_hit_action(hit_code, config=cfg_pycharm) is True
    assert launched[-1] == ["pycharm", "--line", "42", "/src/app.py"]

    # 6. Generic JetBrains preference (`code_editor="jetbrains"`) beats VS Code when both installed
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd in {"code", "pycharm"} else None)
    cfg_jetbrains = AppConfig(code_editor="jetbrains")
    assert execute_hit_action(hit_code, config=cfg_jetbrains) is True
    assert launched[-1] == ["pycharm", "--line", "42", "/src/app.py"]

    # 7. Generic JetBrains preference resolves next installed IDE (`idea`)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd in {"code", "idea"} else None)
    assert execute_hit_action(hit_code, config=cfg_jetbrains) is True
    assert launched[-1] == ["idea", "--line", "42", "/src/app.py"]


def test_document_viewer_actions_deep_link(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[list[str]] = []

    def mock_popen(args: list[str], **_kwargs: Any) -> None:
        launched.append(args)

    monkeypatch.setattr(subprocess, "Popen", mock_popen)

    hit_pdf = SearchHit(path="/docs/manual.pdf", kind=FileKind.PDF, score=1.0, loc=5.0)

    # 1. Papers (`papers -i page -- file`)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "papers" else None)
    assert execute_hit_action(hit_pdf) is True
    assert launched[-1] == ["papers", "-i", "5", "--", "/docs/manual.pdf"]

    # 2. Okular (`okular -p page -- file`)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "okular" else None)
    assert execute_hit_action(hit_pdf) is True
    assert launched[-1] == ["okular", "-p", "5", "--", "/docs/manual.pdf"]

    # 3. Evince (`evince -i page -- file`)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "evince" else None)
    assert execute_hit_action(hit_pdf) is True
    assert launched[-1] == ["evince", "-i", "5", "--", "/docs/manual.pdf"]

    # 4. Zathura (`zathura -P page -- file`)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "zathura" else None)
    assert execute_hit_action(hit_pdf) is True
    assert launched[-1] == ["zathura", "-P", "5", "--", "/docs/manual.pdf"]


def test_media_player_actions_deep_link(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[list[str]] = []

    def mock_popen(args: list[str], **_kwargs: Any) -> None:
        launched.append(args)

    monkeypatch.setattr(subprocess, "Popen", mock_popen)

    hit_video = SearchHit(path="/media/demo.mp4", kind=FileKind.VIDEO, score=1.0, loc=75.0)

    # 1. MPV (`mpv --start=sec -- file`)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "mpv" else None)
    assert execute_hit_action(hit_video) is True
    assert launched[-1] == ["mpv", "--start=75", "--", "/media/demo.mp4"]

    # 2. VLC (`vlc --start-time=sec -- file`)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "vlc" else None)
    assert execute_hit_action(hit_video) is True
    assert launched[-1] == ["vlc", "--start-time=75", "--", "/media/demo.mp4"]

    # 3. Fallback to default handler
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    assert execute_hit_action(hit_video) is True
    assert launched[-1] == ["xdg-open", "/media/demo.mp4"]


def test_file_manager_reveal_action(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[list[str]] = []
    dbus_calls: list[tuple[str, str, str, str, list[str], str]] = []

    def mock_popen(args: list[str], **_kwargs: Any) -> None:
        launched.append(args)

    class FakeBus:
        def __init__(self, *, connected: bool) -> None:
            self._connected = connected

        def isConnected(self) -> bool:  # noqa: N802
            return self._connected

    class FakeReply:
        def __init__(self, msg_type: QDBusMessage.MessageType) -> None:
            self._type = msg_type

        def type(self) -> QDBusMessage.MessageType:
            return self._type

    class FakeDBusInterface:
        def __init__(self, service: str, path: str, interface: str, _bus: object) -> None:
            self._service = service
            self._path = path
            self._interface = interface

        def call(self, method: str, uris: list[str], startup_id: str) -> FakeReply:
            dbus_calls.append((self._service, self._path, self._interface, method, uris, startup_id))
            return FakeReply(QDBusMessage.MessageType.ReplyMessage)

    monkeypatch.setattr(subprocess, "Popen", mock_popen)
    target_path = "/home/user/document.txt"
    expected_resolved = Path(target_path).resolve()

    # 1. QtDBus org.freedesktop.FileManager1.ShowItems invocation succeeds
    monkeypatch.setattr("semsearch.services.actions.QDBusConnection.sessionBus", lambda: FakeBus(connected=True))
    monkeypatch.setattr("semsearch.services.actions.QDBusInterface", FakeDBusInterface)
    assert reveal_in_file_manager(target_path) is True
    assert dbus_calls == [
        (
            "org.freedesktop.FileManager1",
            "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1",
            "ShowItems",
            [expected_resolved.as_uri()],
            "",
        )
    ]
    assert launched == []

    # 2. Dolphin selection fallback when session D-Bus is unavailable
    monkeypatch.setattr("semsearch.services.actions.QDBusConnection.sessionBus", lambda: FakeBus(connected=False))
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "dolphin" else None)
    assert reveal_in_file_manager(target_path) is True
    assert launched[-1] == ["dolphin", "--select", str(expected_resolved)]

    # 3. Nautilus selection fallback
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "nautilus" else None)
    assert reveal_in_file_manager(target_path) is True
    assert launched[-1] == ["nautilus", "--select", str(expected_resolved)]

    # 4. Parent directory fallback via xdg-open when no known file manager binary is present
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    assert reveal_in_file_manager(target_path) is True
    assert launched[-1] == ["xdg-open", str(expected_resolved.parent)]
