"""File filtering, secret detection, and classification rules."""

import re
from pathlib import Path

from semsearch.core.types import FileKind

SKIPPED_FOLDER_NAMES = {
    "node_modules",
    "bower_components",
    "venv",
    "site-packages",
    "__pycache__",
    "DerivedData",
    "Pods",
    "Carthage",
    "build",
    "dist",
    "target",
    "vendor",
    ".git",
    ".noindex",
}

SECRET_EXTENSIONS = {
    "pem",
    "key",
    "p12",
    "pfx",
    "keystore",
    "jks",
    "ppk",
    "kdbx",
    "gpg",
    "asc",
    "crt",
    "cer",
    "der",
    "p8",
    "mobileprovision",
}

SECRET_PREFIXES = (
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    ".env",
    "secret",
    "credential",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "htpasswd",
    ".htpasswd",
)

PROJECT_MARKERS = {
    ".git",
    "package.json",
    "Package.swift",
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
    "composer.json",
    "CMakeLists.txt",
}

PROJECT_BUNDLE_EXTENSIONS = {".xcodeproj", ".xcworkspace"}

SCREENSHOT_PREFIXES = (
    "screenshot",
    "screen shot",
    "cleanshot",
    "scr-",
    "captura de pantalla",
    "captura",
)

CAMERA_PREFIXES = (
    "img",
    "dsc",
    "pxl",
    "mvimg",
    "vid",
    "gopr",
    "dji",
    "photo",
    "whatsapp",
    "screenshot",
    "captura",
)

IMAGE_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "webp",
    "gif",
    "bmp",
    "tiff",
    "tif",
    "heic",
    "avif",
    "svg",
}
PDF_EXTENSIONS = {"pdf"}
DOC_EXTENSIONS = {
    "txt",
    "text",
    "md",
    "markdown",
    "rst",
    "rtf",
    "docx",
    "doc",
    "odt",
    "html",
    "htm",
}
CODE_DOC_EXTENSIONS = {"md", "markdown", "rst"}
AUDIO_EXTENSIONS = {"mp3", "m4a", "wav", "aac", "flac", "ogg", "wma", "aiff"}
VIDEO_EXTENSIONS = {"mp4", "mov", "mkv", "avi", "webm", "m4v", "wmv"}
CODE_EXTENSIONS = {
    "py",
    "ipynb",
    "c",
    "h",
    "cc",
    "cpp",
    "cxx",
    "hpp",
    "hh",
    "m",
    "mm",
    "swift",
    "ts",
    "tsx",
    "cts",
    "mts",
    "js",
    "jsx",
    "mjs",
    "cjs",
    "go",
    "rs",
    "java",
    "kt",
    "kts",
    "scala",
    "rb",
    "php",
    "cs",
    "fs",
    "sh",
    "bash",
    "zsh",
    "fish",
    "ps1",
    "lua",
    "pl",
    "r",
    "jl",
    "dart",
    "sql",
    "vue",
    "svelte",
    "css",
    "scss",
    "sass",
    "less",
    "json",
    "jsonl",
    "yaml",
    "yml",
    "toml",
    "xml",
    "plist",
    "proto",
    "graphql",
    "tf",
    "nix",
    "zig",
    "hs",
    "ex",
    "exs",
    "erl",
    "clj",
    "elm",
    "ml",
    "vim",
    "el",
    "asm",
}
CODE_FILENAMES = {
    "makefile",
    "gnumakefile",
    "dockerfile",
    "containerfile",
    "justfile",
    "rakefile",
    "gemfile",
    "podfile",
    "brewfile",
    "procfile",
    "vagrantfile",
    "jenkinsfile",
}

DATA_EXTENSIONS = {
    "json",
    "jsonl",
    "xml",
    "toml",
    "plist",
    "csv",
    "tsv",
    "lock",
}


def is_secret(path: str) -> bool:
    """Return True if path represents a secret, private key, or credential file."""
    p = Path(path)
    ext = p.suffix.lstrip(".").lower()
    if ext in SECRET_EXTENSIONS:
        return True
    name = p.name.lower()
    return any(name.startswith(prefix) for prefix in SECRET_PREFIXES)


def is_code_project_root(path: str) -> bool:
    """Return True if directory contains common code project manifest files."""
    p = Path(path)
    if not p.is_dir():
        return False
    try:
        for entry in p.iterdir():
            if entry.name in PROJECT_MARKERS or entry.suffix in PROJECT_BUNDLE_EXTENSIONS:
                return True
    except OSError:
        return False
    return False


def looks_machine_named(stem: str) -> bool:
    """Return True if stem looks like a machine-generated filename rather than a camera photo."""
    name = stem.lower()
    if any(name.startswith(prefix) for prefix in CAMERA_PREFIXES):
        return False
    if re.match(r"^[0-9a-f]{8,}([-_][0-9a-f]{4,})*$", name):
        return True
    return bool(re.match(r"^[a-z]{0,16}[-_. ]?\d{3,}([-_.]\d+)*$", name))


def is_dataset_folder(paths: list[str]) -> bool:
    """Detect if paths contain >= 300 media files of the same kind with >= 70% machine-named stems."""
    by_kind: dict[FileKind, list[str]] = {
        FileKind.IMAGE: [],
        FileKind.AUDIO: [],
        FileKind.VIDEO: [],
    }
    for path in paths:
        kind = classify_file(path)
        if kind in by_kind:
            by_kind[kind].append(Path(path).stem)

    for kind_stems in by_kind.values():
        if len(kind_stems) >= 300:
            machine_count = sum(1 for stem in kind_stems if looks_machine_named(stem))
            if (machine_count / len(kind_stems)) >= 0.7:
                return True
    return False


def classify_file(path: str, is_code_mode: bool = False) -> FileKind | None:
    """Map a file path to its FileKind classification, or None if unsupported."""
    p = Path(path)
    ext = p.suffix.lstrip(".").lower()
    name = p.name.lower()

    if name in CODE_FILENAMES or name.startswith("dockerfile"):
        return FileKind.CODE

    if is_code_mode and ext in CODE_DOC_EXTENSIONS:
        return FileKind.CODE

    if ext in IMAGE_EXTENSIONS:
        if any(name.startswith(prefix) for prefix in SCREENSHOT_PREFIXES):
            return FileKind.SCREENSHOT
        return FileKind.IMAGE

    if ext in PDF_EXTENSIONS:
        return FileKind.PDF

    if ext in DOC_EXTENSIONS:
        return FileKind.DOC

    if ext in AUDIO_EXTENSIONS:
        return FileKind.AUDIO

    if ext in VIDEO_EXTENSIONS:
        return FileKind.VIDEO

    if ext in CODE_EXTENSIONS:
        return FileKind.CODE

    return None


def is_inside_code_project(path: str) -> bool:
    """Return True if path is inside a recognized code project."""
    parent = Path(path).resolve().parent
    root = Path(parent.root)
    while parent != root:
        if is_code_project_root(str(parent)):
            return True
        parent = parent.parent
    return False


def is_path_excluded(path: str, excluded_folders: list[str] | set[str] | None) -> bool:
    """Return True if path is inside any user-configured excluded folder."""
    if not excluded_folders:
        return False
    p = Path(path)
    resolved = str(p.resolve()) if p.is_absolute() else str(p)
    parts_set = set(p.parts)
    for excl in excluded_folders:
        clean = excl.strip()
        if not clean:
            continue
        if clean.startswith(("/", "~")):
            excl_resolved = str(Path(clean).expanduser().resolve())
            if resolved == excl_resolved or resolved.startswith(f"{excl_resolved}/"):
                return True
        elif clean in parts_set:
            return True
    return False


def should_skip(
    path: str,
    is_code_mode: bool,
    excluded_folders: list[str] | set[str] | None = None,
    excluded_types: list[str] | set[str] | None = None,
) -> tuple[bool, str]:
    """Determine whether to skip a file during indexing."""
    p = Path(path)

    for part in p.parts[:-1]:
        if part in SKIPPED_FOLDER_NAMES or part.endswith(".noindex"):
            return True, f"inside skipped folder {part}"

    if is_path_excluded(path, excluded_folders):
        return True, "inside excluded folder"

    if is_secret(path):
        return True, "secret or credential file"

    ext = p.suffix.lstrip(".").lower()
    name = p.name.lower()
    if is_code_mode and (ext in DATA_EXTENSIONS or name.endswith(".lock") or "-lock." in name):
        return True, "data format in code mode"

    kind = classify_file(path, is_code_mode=is_code_mode)
    if kind is None:
        return True, "unsupported file kind"

    if excluded_types:
        norm_types = {t.lstrip(".").strip().lower() for t in excluded_types if t.strip()}
        if ext in norm_types or kind.value in norm_types:
            return True, f"excluded file type {ext or kind.value}"

    if is_code_mode:
        if kind != FileKind.CODE:
            return True, "not a code file"
    else:
        if kind == FileKind.CODE:
            return True, "code file in general mode"
        if kind != FileKind.SCREENSHOT and is_inside_code_project(path):
            return True, "non-screenshot inside code project"

    return False, ""
