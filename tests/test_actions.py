"""Tests for Linux desktop actions, editor jumps, and deep links."""

from pathlib import Path
import pytest

from semsearch.config import AppConfig
from semsearch.core.types import FileKind, SearchHit
from semsearch.services.actions import (
    copy_file_to_clipboard,
    copy_to_clipboard,
    execute_hit_action,
    open_code_at,
    open_file,
    open_media_at,
    open_pdf_at,
    reveal_in_file_manager,
)


def test_open_file(monkeypatch):
    launched = []

    def mock_popen(args, **kwargs):
        launched.append(args)
        return None

    monkeypatch.setattr("subprocess.Popen", mock_popen)
    assert open_file("/path/to/doc.pdf") is True
    assert launched == [["xdg-open", "/path/to/doc.pdf"]]


def test_open_media_at(monkeypatch):
    launched = []

    def mock_popen(args, **kwargs):
        launched.append(args)
        return None

    monkeypatch.setattr("subprocess.Popen", mock_popen)

    # When mpv exists
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "mpv" else None)
    assert open_media_at("/path/to/video.mp4", 125.7) is True
    assert launched[-1] == ["mpv", "--start=125", "/path/to/video.mp4"]

    # When vlc exists (and mpv does not)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "vlc" else None)
    assert open_media_at("/path/to/video.mp4", 60.0) is True
    assert launched[-1] == ["vlc", "--start-time=60", "/path/to/video.mp4"]

    # When neither exists, fallback to xdg-open
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    assert open_media_at("/path/to/video.mp4", 45.0) is True
    assert launched[-1] == ["xdg-open", "/path/to/video.mp4"]


def test_open_pdf_at(monkeypatch):
    launched = []

    def mock_popen(args, **kwargs):
        launched.append(args)
        return None

    monkeypatch.setattr("subprocess.Popen", mock_popen)

    # When okular exists
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "okular" else None)
    assert open_pdf_at("/doc.pdf", 12) is True
    assert launched[-1] == ["okular", "-p", "12", "/doc.pdf"]

    # When evince exists
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "evince" else None)
    assert open_pdf_at("/doc.pdf", 5) is True
    assert launched[-1] == ["evince", "-p", "5", "/doc.pdf"]

    # When zathura exists
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "zathura" else None)
    assert open_pdf_at("/doc.pdf", 3) is True
    assert launched[-1] == ["zathura", "-P", "3", "/doc.pdf"]

    # Fallback
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    assert open_pdf_at("/doc.pdf", 1) is True
    assert launched[-1] == ["xdg-open", "/doc.pdf"]


def test_open_code_at(monkeypatch):
    launched = []

    def mock_popen(args, **kwargs):
        launched.append(args)
        return None

    monkeypatch.setattr("subprocess.Popen", mock_popen)

    # VS Code explicit preference
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "code" else None)
    assert open_code_at("/src/app.py", line=42, editor_preference="code") is True
    assert launched[-1] == ["code", "-g", "/src/app.py:42"]

    # Cursor
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "cursor" else None)
    assert open_code_at("/src/app.py", line=15, editor_preference="auto") is True
    assert launched[-1] == ["cursor", "-g", "/src/app.py:15"]

    # Zed
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "zed" else None)
    assert open_code_at("/src/app.py", line=20, editor_preference="zed") is True
    assert launched[-1] == ["zed", "/src/app.py:20"]

    # JetBrains IDE (e.g. pycharm)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "pycharm" else None)
    assert open_code_at("/src/app.py", line=99, editor_preference="auto") is True
    assert launched[-1] == ["pycharm", "--line", "99", "/src/app.py"]

    # Fallback to xdg-open
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    assert open_code_at("/src/app.py", line=10) is True
    assert launched[-1] == ["xdg-open", "/src/app.py"]


def test_reveal_in_file_manager(monkeypatch):
    launched = []

    def mock_popen(args, **kwargs):
        launched.append(args)
        return None

    monkeypatch.setattr("subprocess.Popen", mock_popen)

    # DBus success
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "dbus-send" else None)

    class MockRunResult:
        returncode = 0

    monkeypatch.setattr("subprocess.run", lambda args, **kwargs: MockRunResult())
    assert reveal_in_file_manager("/home/user/test.txt") is True

    # Dolphin fallback
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "dolphin" else None)
    assert reveal_in_file_manager("/home/user/test.txt") is True
    assert launched[-1] == ["dolphin", "--select", str(Path("/home/user/test.txt").resolve())]

    # Nautilus fallback
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "nautilus" else None)
    assert reveal_in_file_manager("/home/user/test.txt") is True
    assert launched[-1] == ["nautilus", "--select", str(Path("/home/user/test.txt").resolve())]

    # xdg-open parent fallback
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    assert reveal_in_file_manager("/home/user/test.txt") is True
    assert launched[-1] == ["xdg-open", str(Path("/home/user/test.txt").resolve().parent)]


def test_copy_to_clipboard(monkeypatch):
    captured = []

    class MockProc:
        returncode = 0

        def communicate(self, input):
            captured.append(input)

    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "wl-copy" else None)
    monkeypatch.setattr("subprocess.Popen", lambda args, **kwargs: MockProc())

    assert copy_to_clipboard("hello world") is True
    assert captured == [b"hello world"]

    assert copy_file_to_clipboard("/home/user/test.txt") is True
    assert captured[-1] == f"file://{Path('/home/user/test.txt').resolve()}".encode("utf-8")


def test_execute_hit_action(monkeypatch):
    actions_called = []

    monkeypatch.setattr("semsearch.services.actions.open_code_at", lambda p, line, editor_preference: actions_called.append(("code", p, line)))
    monkeypatch.setattr("semsearch.services.actions.open_pdf_at", lambda p, page: actions_called.append(("pdf", p, page)))
    monkeypatch.setattr("semsearch.services.actions.open_media_at", lambda p, seconds: actions_called.append(("media", p, seconds)))
    monkeypatch.setattr("semsearch.services.actions.open_file", lambda p: actions_called.append(("file", p)))

    config = AppConfig(code_editor="code")

    # Code hit with focus
    hit_code = SearchHit(path="/src/main.py", kind=FileKind.CODE, score=1.0, focus=45.0)
    execute_hit_action(hit_code, config)
    assert actions_called[-1] == ("code", "/src/main.py", 45)

    # PDF hit with page
    hit_pdf = SearchHit(path="/docs/paper.pdf", kind=FileKind.PDF, score=1.0, loc=7.0)
    execute_hit_action(hit_pdf, config)
    assert actions_called[-1] == ("pdf", "/docs/paper.pdf", 7)

    # Video hit with seconds
    hit_video = SearchHit(path="/media/demo.mp4", kind=FileKind.VIDEO, score=1.0, loc=180.0)
    execute_hit_action(hit_video, config)
    assert actions_called[-1] == ("media", "/media/demo.mp4", 180.0)

    # Standard doc
    hit_doc = SearchHit(path="/docs/notes.txt", kind=FileKind.DOC, score=1.0)
    execute_hit_action(hit_doc, config)
    assert actions_called[-1] == ("file", "/docs/notes.txt")
