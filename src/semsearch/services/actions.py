"""Linux desktop deep link actions, media playback, and editor integration."""

from pathlib import Path
import shutil
import subprocess

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


def _launch(args: list[str]) -> bool:
    """Launch detached background process."""
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def open_file(path: str) -> bool:
    """Open file using system default handler (xdg-open)."""
    return _launch(["xdg-open", path])


def open_media_at(path: str, seconds: float) -> bool:
    """Open media file at specific timestamp using mpv, vlc, or xdg-open."""
    sec = max(int(seconds), 0)
    if shutil.which("mpv"):
        return _launch(["mpv", f"--start={sec}", path])
    if shutil.which("vlc"):
        return _launch(["vlc", f"--start-time={sec}", path])
    return open_file(path)


def open_pdf_at(path: str, page: int) -> bool:
    """Open PDF document at specific page using okular, evince, zathura, or xdg-open."""
    target_page = max(page, 1)
    if shutil.which("okular"):
        return _launch(["okular", "-p", str(target_page), path])
    if shutil.which("evince"):
        return _launch(["evince", "-p", str(target_page), path])
    if shutil.which("zathura"):
        return _launch(["zathura", "-P", str(target_page), path])
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

    if pref != "auto" and shutil.which(pref):
        return _launch(_editor_command(pref, path, target_line))

    for candidate in ("code", "cursor", "zed", "subl", *JETBRAINS_IDES):
        if shutil.which(candidate):
            return _launch(_editor_command(candidate, path, target_line))

    return open_file(path)


def reveal_in_file_manager(path: str) -> bool:
    """Highlight and select file in the system file manager via DBus or CLI fallbacks."""
    abs_path = str(Path(path).resolve())
    uri = f"file://{abs_path}"

    if shutil.which("dbus-send"):
        try:
            res = subprocess.run(
                [
                    "dbus-send",
                    "--session",
                    "--dest=org.freedesktop.FileManager1",
                    "--type=method_call",
                    "/org/freedesktop/FileManager1",
                    "org.freedesktop.FileManager1.ShowItems",
                    f"array:string:{uri}",
                    'string:""',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if res.returncode == 0:
                return True
        except OSError:
            pass

    for fm in ("dolphin", "nautilus", "nemo"):
        if shutil.which(fm):
            return _launch([fm, "--select", abs_path])

    return open_file(str(Path(abs_path).parent))


def copy_to_clipboard(text: str) -> bool:
    """Copy text content to system clipboard via wl-copy or xclip."""
    for binary, cmd in (("wl-copy", ["wl-copy"]), ("xclip", ["xclip", "-selection", "clipboard"])):
        if shutil.which(binary):
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                proc.communicate(input=text.encode("utf-8"))
                return proc.returncode == 0
            except OSError:
                pass
    return False


def copy_file_to_clipboard(path: str) -> bool:
    """Copy file URI to system clipboard."""
    abs_path = str(Path(path).resolve())
    return copy_to_clipboard(f"file://{abs_path}")


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
