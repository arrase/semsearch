"""Linux desktop deep link actions, media playback, terminal launch, and editor integration."""

import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
from PySide6.QtGui import QGuiApplication

from semsearch.config import AppConfig
from semsearch.core.types import FileKind, SearchHit

JETBRAINS_IDES = [
    "pycharm",
    "idea",
    "clion",
    "webstorm",
    "goland",
    "rustrover",
    "rider",
]

STANDARD_TERMINALS: tuple[str, ...] = (
    "xdg-terminal-exec",
    "kitty",
    "alacritty",
    "ghostty",
    "konsole",
    "gnome-terminal",
    "wezterm",
    "xfce4-terminal",
    "xterm",
)

_PDF_VIEWER_ARGS: dict[str, str] = {
    "papers": "-i",
    "okular": "-p",
    "evince": "-i",
    "zathura": "-P",
}
_DEFAULT_PDF_VIEWERS: tuple[str, ...] = ("papers", "okular", "evince", "zathura")


def _launch(args: list[str], *, cwd: str | None = None) -> bool:
    """Launch detached background process."""
    try:
        subprocess.Popen(args, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    else:
        return True


def open_file(path: str) -> bool:
    """Open file using system default handler (xdg-open)."""
    return _launch(["xdg-open", path])


def open_media_at(path: str, seconds: float) -> bool:
    """Open media file at specific timestamp using mpv, vlc, or xdg-open."""
    sec = max(int(seconds), 0)
    if shutil.which("mpv"):
        return _launch(["mpv", f"--start={sec}", "--", path])
    if shutil.which("vlc"):
        return _launch(["vlc", f"--start-time={sec}", "--", path])
    return open_file(path)


def _default_pdf_viewer() -> str | None:
    if not shutil.which("xdg-mime"):
        return None
    try:
        res = subprocess.run(
            ["xdg-mime", "query", "default", "application/pdf"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    desktop = res.stdout.strip().lower()
    for viewer in _DEFAULT_PDF_VIEWERS:
        if viewer in desktop:
            return viewer
    return None


def open_pdf_at(path: str, page: int) -> bool:
    """Open PDF document at specific page preferring the system default XDG PDF viewer."""
    target_page = max(page, 1)
    preferred = _default_pdf_viewer()
    candidates = (
        (preferred, *(v for v in _DEFAULT_PDF_VIEWERS if v != preferred))
        if preferred is not None
        else _DEFAULT_PDF_VIEWERS
    )
    for viewer in candidates:
        if shutil.which(viewer):
            flag = _PDF_VIEWER_ARGS[viewer]
            return _launch([viewer, flag, str(target_page), "--", path])
    return open_file(path)


def _editor_command(editor: str, path: str, line: int) -> list[str]:
    if editor in ("code", "cursor"):
        return [editor, "-g", f"{path}:{line}"]
    if editor in ("zed", "subl"):
        return [editor, f"{path}:{line}"]
    if editor in JETBRAINS_IDES:
        return [editor, "--line", str(line), path]
    return [editor, path]


def open_code_at(path: str, line: int = 1, editor_preference: str = "auto") -> bool:
    """Open code file at specific line number using preferred or auto-detected code editor."""
    target_line = max(line, 1)
    pref = editor_preference.strip().lower()

    if pref == "jetbrains":
        for ide in JETBRAINS_IDES:
            if shutil.which(ide):
                return _launch(_editor_command(ide, path, target_line))
    elif pref != "auto" and shutil.which(pref):
        return _launch(_editor_command(pref, path, target_line))

    for candidate in ("code", "cursor", "zed", "subl", *JETBRAINS_IDES):
        if shutil.which(candidate):
            return _launch(_editor_command(candidate, path, target_line))

    return open_file(path)


def reveal_in_file_manager(path: str) -> bool:
    """Highlight and select file in the system file manager via QtDBus or CLI fallbacks."""
    resolved = Path(path).resolve()
    abs_path = str(resolved)
    uri = resolved.as_uri()

    bus = QDBusConnection.sessionBus()
    if bus.isConnected():
        iface = QDBusInterface(
            "org.freedesktop.FileManager1",
            "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1",
            bus,
        )
        reply = iface.call("ShowItems", [uri], "")
        if reply.type() == QDBusMessage.MessageType.ReplyMessage:
            return True

    for fm in ("dolphin", "nautilus", "nemo"):
        if shutil.which(fm):
            return _launch([fm, "--select", abs_path])

    return open_file(str(resolved.parent))


def _copy_text_cli(text: str) -> bool:
    for binary, cmd in (("wl-copy", ["wl-copy"]), ("xclip", ["xclip", "-selection", "clipboard"])):
        if shutil.which(binary):
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                proc.communicate(input=text.encode("utf-8"))
            except OSError:
                pass
            else:
                return proc.returncode == 0
    return False


def copy_to_clipboard(text: str) -> bool:
    """Copy text content to system clipboard via Qt or wl-copy/xclip fallback."""
    if QGuiApplication.instance() is not None:
        QGuiApplication.clipboard().setText(text)
        return True
    return _copy_text_cli(text)


def copy_file_to_clipboard(path: str) -> bool:
    """Copy file URL and URI text to system clipboard via Qt or CLI fallback."""
    resolved = Path(path).resolve()
    abs_path = str(resolved)
    uri = resolved.as_uri()
    if QGuiApplication.instance() is not None:
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(abs_path)])
        mime.setText(uri)
        QGuiApplication.clipboard().setMimeData(mime)
        return True
    return _copy_text_cli(uri)


def _terminal_command(terminal: str, target_dir: str) -> list[str]:
    commands: dict[str, list[str]] = {
        "xdg-terminal-exec": ["xdg-terminal-exec", f"--dir={target_dir}"],
        "kitty": ["kitty", "--directory", target_dir],
        "alacritty": ["alacritty", "--working-directory", target_dir],
        "ghostty": ["ghostty", f"--working-directory={target_dir}"],
        "konsole": ["konsole", "--workdir", target_dir],
        "gnome-terminal": ["gnome-terminal", f"--working-directory={target_dir}"],
        "wezterm": ["wezterm", "start", "--cwd", target_dir],
        "xfce4-terminal": ["xfce4-terminal", "--working-directory", target_dir],
        "xterm": ["xterm"],
    }
    return commands[terminal]


def open_terminal_at(path: str) -> bool:
    """Open a standard Linux terminal emulator at the directory containing path."""
    resolved = Path(path).resolve()
    target_dir = str(resolved if resolved.is_dir() else resolved.parent)
    for terminal in STANDARD_TERMINALS:
        if shutil.which(terminal):
            return _launch(_terminal_command(terminal, target_dir), cwd=target_dir)
    return False


def execute_hit_action(hit: SearchHit, config: AppConfig | None = None) -> bool:
    """Dispatch the appropriate deep link desktop action for a given search hit."""
    pref = config.code_editor if config else "auto"

    if hit.kind == FileKind.CODE and (hit.focus > 0 or hit.loc > 0):
        target_line = int(hit.focus) if hit.focus > 0 else int(hit.loc)
        return open_code_at(hit.path, line=target_line, editor_preference=pref)

    if hit.kind == FileKind.PDF and hit.loc > 0:
        return open_pdf_at(hit.path, page=int(hit.loc))

    if hit.kind in (FileKind.AUDIO, FileKind.VIDEO) and hit.loc > 0:
        return open_media_at(hit.path, seconds=hit.loc)

    return open_file(hit.path)
