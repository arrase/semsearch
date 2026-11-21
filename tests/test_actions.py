"""Tests for Linux desktop actions, editor jumps, PDF/media deep links, clipboard, and terminal launch."""

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtDBus import QDBusMessage

from semsearch.config import AppConfig
from semsearch.core.types import FileKind, SearchHit
from semsearch.services import open_terminal_at as exported_open_terminal_at
from semsearch.services.actions import (
    copy_file_to_clipboard,
    copy_to_clipboard,
    execute_hit_action,
    open_code_at,
    open_file,
    open_media_at,
    open_pdf_at,
    open_terminal_at,
    reveal_in_file_manager,
)


class _CompletedProc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


def test_open_file(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[list[str]] = []

    def mock_popen(args: list[str], **_kwargs: Any) -> None:
        launched.append(args)

    monkeypatch.setattr("subprocess.Popen", mock_popen)
    assert open_file("/path/to/doc.pdf") is True
    assert launched == [["xdg-open", "/path/to/doc.pdf"]]


def test_open_media_at(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[list[str]] = []

    def mock_popen(args: list[str], **_kwargs: Any) -> None:
        launched.append(args)

    monkeypatch.setattr("subprocess.Popen", mock_popen)

    # When mpv exists
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "mpv" else None)
    assert open_media_at("/path/to/video.mp4", 125.7) is True
    assert launched[-1] == ["mpv", "--start=125", "--", "/path/to/video.mp4"]

    # When vlc exists (and mpv does not)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "vlc" else None)
    assert open_media_at("/path/to/video.mp4", 60.0) is True
    assert launched[-1] == ["vlc", "--start-time=60", "--", "/path/to/video.mp4"]

    # When neither exists, fallback to xdg-open
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    assert open_media_at("/path/to/video.mp4", 45.0) is True
    assert launched[-1] == ["xdg-open", "/path/to/video.mp4"]


def test_open_pdf_at(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[list[str]] = []

    def mock_popen(args: list[str], **_kwargs: Any) -> None:
        launched.append(args)

    monkeypatch.setattr("subprocess.Popen", mock_popen)

    # Respect default XDG viewer (papers preferred over okular when xdg-mime returns org.gnome.Papers.desktop)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd in {"xdg-mime", "papers", "okular"} else None)
    monkeypatch.setattr("subprocess.run", lambda _args, **_kwargs: _CompletedProc("org.gnome.Papers.desktop\n", 0))
    assert open_pdf_at("/doc.pdf", 8) is True
    assert launched[-1] == ["papers", "-i", "8", "--", "/doc.pdf"]

    # Default XDG viewer selects okular even when papers is installed
    monkeypatch.setattr("subprocess.run", lambda _args, **_kwargs: _CompletedProc("okularApplication_pdf.desktop\n", 0))
    assert open_pdf_at("/doc.pdf", 12) is True
    assert launched[-1] == ["okular", "-p", "12", "--", "/doc.pdf"]

    # When evince exists (uses -i physical 1-based page index)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "evince" else None)
    assert open_pdf_at("/doc.pdf", 5) is True
    assert launched[-1] == ["evince", "-i", "5", "--", "/doc.pdf"]

    # When zathura exists
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "zathura" else None)
    assert open_pdf_at("/doc.pdf", 3) is True
    assert launched[-1] == ["zathura", "-P", "3", "--", "/doc.pdf"]

    # Fallback
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    assert open_pdf_at("/doc.pdf", 1) is True
    assert launched[-1] == ["xdg-open", "/doc.pdf"]


def test_open_code_at(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[list[str]] = []

    def mock_popen(args: list[str], **_kwargs: Any) -> None:
        launched.append(args)

    monkeypatch.setattr("subprocess.Popen", mock_popen)

    # VS Code explicit preference
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "code" else None)
    assert open_code_at("/src/app.py", line=42, editor_preference="code") is True
    assert launched[-1] == ["code", "-g", "/src/app.py:42"]

    # JetBrains preference chooses pycharm even when VS Code is also installed
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd in {"code", "pycharm"} else None)
    assert open_code_at("/src/app.py", line=77, editor_preference="jetbrains") is True
    assert launched[-1] == ["pycharm", "--line", "77", "/src/app.py"]

    # Cursor
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "cursor" else None)
    assert open_code_at("/src/app.py", line=15, editor_preference="auto") is True
    assert launched[-1] == ["cursor", "-g", "/src/app.py:15"]

    # Zed
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "zed" else None)
    assert open_code_at("/src/app.py", line=20, editor_preference="zed") is True
    assert launched[-1] == ["zed", "/src/app.py:20"]

    # JetBrains IDE auto-detection
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "pycharm" else None)
    assert open_code_at("/src/app.py", line=99, editor_preference="auto") is True
    assert launched[-1] == ["pycharm", "--line", "99", "/src/app.py"]

    # Fallback to xdg-open
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    assert open_code_at("/src/app.py", line=10) is True
    assert launched[-1] == ["xdg-open", "/src/app.py"]


def test_reveal_in_file_manager_dbus_escapes_spaces_and_commas(monkeypatch: pytest.MonkeyPatch) -> None:
    dbus_calls: list[tuple[str, Any, Any]] = []

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
            self.service = service
            self.path = path
            self.interface = interface

        def call(self, method: str, uris: list[str], startup_id: str) -> FakeReply:
            dbus_calls.append((method, uris, startup_id))
            return FakeReply(QDBusMessage.MessageType.ReplyMessage)

    monkeypatch.setattr("semsearch.services.actions.QDBusConnection.sessionBus", lambda: FakeBus(connected=True))
    monkeypatch.setattr("semsearch.services.actions.QDBusInterface", FakeDBusInterface)

    tricky_path = "/tmp/my report, v2.pdf"
    assert reveal_in_file_manager(tricky_path) is True
    expected_uri = Path(tricky_path).resolve().as_uri()
    assert expected_uri.endswith("/my%20report%2C%20v2.pdf")
    assert dbus_calls == [("ShowItems", [expected_uri], "")]


def test_reveal_in_file_manager_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[list[str]] = []

    def mock_popen(args: list[str], **_kwargs: Any) -> None:
        launched.append(args)

    class DisconnectedBus:
        def isConnected(self) -> bool:  # noqa: N802
            return False

    monkeypatch.setattr("subprocess.Popen", mock_popen)
    monkeypatch.setattr("semsearch.services.actions.QDBusConnection.sessionBus", DisconnectedBus)

    # Dolphin fallback
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "dolphin" else None)
    assert reveal_in_file_manager("/home/user/test.txt") is True
    assert launched[-1] == ["dolphin", "--select", str(Path("/home/user/test.txt").resolve())]

    # Nautilus fallback
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "nautilus" else None)
    assert reveal_in_file_manager("/home/user/test.txt") is True
    assert launched[-1] == ["nautilus", "--select", str(Path("/home/user/test.txt").resolve())]

    # xdg-open parent fallback
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    assert reveal_in_file_manager("/home/user/test.txt") is True
    assert launched[-1] == ["xdg-open", str(Path("/home/user/test.txt").resolve().parent)]


def test_copy_to_clipboard_cli_and_qt(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. Headless CLI fallback (no QGuiApplication instance)
    captured: list[bytes] = []

    class MockProc:
        returncode = 0

        def communicate(self, **kwargs: Any) -> None:
            captured.append(kwargs["input"])

    monkeypatch.setattr("semsearch.services.actions.QGuiApplication.instance", lambda: None)
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "wl-copy" else None)
    monkeypatch.setattr("subprocess.Popen", lambda _args, **_kwargs: MockProc())

    assert copy_to_clipboard("hello world") is True
    assert captured == [b"hello world"]

    spaced_file = "/home/user/my doc, final.txt"
    assert copy_file_to_clipboard(spaced_file) is True
    assert captured[-1] == Path(spaced_file).resolve().as_uri().encode("utf-8")

    # 2. Active Qt GUI clipboard path (text + file QMimeData URLs)
    class FakeClipboard:
        def __init__(self) -> None:
            self.text_value = ""
            self.mime_data: QMimeData | None = None

        def setText(self, text: str) -> None:  # noqa: N802
            self.text_value = text

        def setMimeData(self, mime: QMimeData) -> None:  # noqa: N802
            self.mime_data = mime

    fake_clip = FakeClipboard()
    monkeypatch.setattr("semsearch.services.actions.QGuiApplication.instance", object)
    monkeypatch.setattr("semsearch.services.actions.QGuiApplication.clipboard", lambda: fake_clip)

    assert copy_to_clipboard("qt text") is True
    assert fake_clip.text_value == "qt text"

    assert copy_file_to_clipboard(spaced_file) is True
    assert fake_clip.mime_data is not None
    expected_resolved = str(Path(spaced_file).resolve())
    assert fake_clip.mime_data.urls() == [QUrl.fromLocalFile(expected_resolved)]
    assert fake_clip.mime_data.text() == Path(spaced_file).resolve().as_uri()


def test_open_terminal_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert exported_open_terminal_at is open_terminal_at

    launched: list[tuple[list[str], str | None]] = []

    def mock_popen(args: list[str], **kwargs: Any) -> None:
        launched.append((args, kwargs.get("cwd")))

    monkeypatch.setattr("subprocess.Popen", mock_popen)

    sample_file = tmp_path / "script.py"
    sample_file.write_text("print(1)", encoding="utf-8")
    expected_dir = str(tmp_path.resolve())

    # File path opens terminal in its parent directory via xdg-terminal-exec
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd in {"xdg-terminal-exec", "xterm"} else None)
    assert open_terminal_at(str(sample_file)) is True
    assert launched[-1] == (["xdg-terminal-exec", f"--dir={expected_dir}"], expected_dir)

    # Directory path opens terminal directly in that directory via kitty
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "kitty" else None)
    assert open_terminal_at(str(tmp_path)) is True
    assert launched[-1] == (["kitty", "--directory", expected_dir], expected_dir)

    # xterm inherits cwd
    monkeypatch.setattr("shutil.which", lambda cmd: cmd if cmd == "xterm" else None)
    assert open_terminal_at(str(sample_file)) is True
    assert launched[-1] == (["xterm"], expected_dir)

    # Returns False if no terminal emulator is available
    monkeypatch.setattr("shutil.which", lambda _cmd: None)
    assert open_terminal_at(str(sample_file)) is False


def test_execute_hit_action(monkeypatch: pytest.MonkeyPatch) -> None:
    actions_called: list[tuple[str, ...]] = []

    monkeypatch.setattr(
        "semsearch.services.actions.open_code_at",
        lambda p, line, editor_preference: actions_called.append(("code", p, str(line), editor_preference)),
    )
    monkeypatch.setattr(
        "semsearch.services.actions.open_pdf_at", lambda p, page: actions_called.append(("pdf", p, str(page)))
    )
    monkeypatch.setattr(
        "semsearch.services.actions.open_media_at", lambda p, seconds: actions_called.append(("media", p, str(seconds)))
    )
    monkeypatch.setattr("semsearch.services.actions.open_file", lambda p: actions_called.append(("file", p)))

    config = AppConfig(code_editor="code")

    # Code hit with focus
    hit_code = SearchHit(path="/src/main.py", kind=FileKind.CODE, score=1.0, focus=45.0)
    execute_hit_action(hit_code, config)

    # PDF hit with page
    hit_pdf = SearchHit(path="/docs/paper.pdf", kind=FileKind.PDF, score=1.0, loc=7.0)
    execute_hit_action(hit_pdf, config)

    # Video hit with seconds
    hit_video = SearchHit(path="/media/demo.mp4", kind=FileKind.VIDEO, score=1.0, loc=180.0)
    execute_hit_action(hit_video, config)

    # Standard doc
    hit_doc = SearchHit(path="/docs/notes.txt", kind=FileKind.DOC, score=1.0)
    execute_hit_action(hit_doc, config)

    assert actions_called == [
        ("code", "/src/main.py", "45", "code"),
        ("pdf", "/docs/paper.pdf", "7"),
        ("media", "/media/demo.mp4", "180.0"),
        ("file", "/docs/notes.txt"),
    ]
