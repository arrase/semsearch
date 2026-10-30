"""End-to-end desktop deep-linking action dispatch tests."""

import subprocess
from typing import Any

import pytest

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

    # 1. VS Code (`code -g file:line`)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "code" else None)
    hit_code = SearchHit(path="/src/app.py", kind=FileKind.CODE, score=1.0, focus=42.0)
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

    # 5. JetBrains IDE PyCharm (`pycharm --line line file`)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "pycharm" else None)
    cfg_pycharm = AppConfig(code_editor="pycharm")
    assert execute_hit_action(hit_code, config=cfg_pycharm) is True
    assert launched[-1] == ["pycharm", "--line", "42", "/src/app.py"]


def test_document_viewer_actions_deep_link(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[list[str]] = []

    def mock_popen(args: list[str], **_kwargs: Any) -> None:
        launched.append(args)

    monkeypatch.setattr(subprocess, "Popen", mock_popen)

    hit_pdf = SearchHit(path="/docs/manual.pdf", kind=FileKind.PDF, score=1.0, loc=5.0)

    # 1. Okular (`okular -p page file`)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "okular" else None)
    assert execute_hit_action(hit_pdf) is True
    assert launched[-1] == ["okular", "-p", "5", "/docs/manual.pdf"]

    # 2. Evince (`evince -p page file`)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "evince" else None)
    assert execute_hit_action(hit_pdf) is True
    assert launched[-1] == ["evince", "-p", "5", "/docs/manual.pdf"]

    # 3. Zathura (`zathura -P page file`)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "zathura" else None)
    assert execute_hit_action(hit_pdf) is True
    assert launched[-1] == ["zathura", "-P", "5", "/docs/manual.pdf"]


def test_media_player_actions_deep_link(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[list[str]] = []

    def mock_popen(args: list[str], **_kwargs: Any) -> None:
        launched.append(args)

    monkeypatch.setattr(subprocess, "Popen", mock_popen)

    hit_video = SearchHit(path="/media/demo.mp4", kind=FileKind.VIDEO, score=1.0, loc=75.0)

    # 1. MPV (`mpv --start=sec file`)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "mpv" else None)
    assert execute_hit_action(hit_video) is True
    assert launched[-1] == ["mpv", "--start=75", "/media/demo.mp4"]

    # 2. VLC (`vlc --start-time=sec file`)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "vlc" else None)
    assert execute_hit_action(hit_video) is True
    assert launched[-1] == ["vlc", "--start-time=75", "/media/demo.mp4"]

    # 3. Fallback to default handler
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    assert execute_hit_action(hit_video) is True
    assert launched[-1] == ["xdg-open", "/media/demo.mp4"]


def test_file_manager_reveal_action(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[list[str]] = []

    def mock_popen(args: list[str], **_kwargs: Any) -> None:
        launched.append(args)

    monkeypatch.setattr(subprocess, "Popen", mock_popen)

    # 1. DBus session invocation succeeds
    class CompletedProc:
        returncode = 0

    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: cmd if cmd == "dbus-send" else None,
    )
    monkeypatch.setattr(subprocess, "run", lambda _args, **_kwargs: CompletedProc())
    assert reveal_in_file_manager("/home/user/document.txt") is True

    # 2. Dolphin selection fallback
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "dolphin" else None)
    assert reveal_in_file_manager("/home/user/document.txt") is True
    assert launched[-1] == ["dolphin", "--select", "/home/user/document.txt"]

    # 3. Nautilus selection fallback
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "nautilus" else None)
    assert reveal_in_file_manager("/home/user/document.txt") is True
    assert launched[-1] == ["nautilus", "--select", "/home/user/document.txt"]
