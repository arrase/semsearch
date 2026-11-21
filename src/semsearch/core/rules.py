"""File filtering, secret detection, and classification rules."""

import re
from collections.abc import Iterable
from pathlib import Path

from semsearch.core.types import FileKind

MAX_TEXT_FILE_BYTES = 10 * 1024 * 1024

SKIPPED_FOLDER_NAMES: frozenset[str] = frozenset(
    {
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
)

SECRET_FOLDER_NAMES: frozenset[str] = frozenset(
    {
        ".ssh",
        ".gnupg",
        ".aws",
        ".kube",
    }
)

SECRET_EXTENSIONS: frozenset[str] = frozenset(
    {
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
        "tfstate",
    }
)

SECRET_PREFIXES: tuple[str, ...] = (
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

SECRET_SUBSTRINGS: tuple[str, ...] = (
    "secret",
    "credential",
    "tfstate",
    "kubeconfig",
    "id_rsa",
    "id_ed25519",
    "_ed25519",
    "_rsa",
)

SECRET_EXACT_NAMES: frozenset[str] = frozenset({"hosts.yml"})

MINIFIED_SUFFIXES: tuple[str, ...] = (
    ".min.js",
    ".min.css",
    ".bundle.js",
    ".chunk.js",
)

PROJECT_MARKERS: frozenset[str] = frozenset(
    {
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
)

PROJECT_BUNDLE_EXTENSIONS: frozenset[str] = frozenset({".xcodeproj", ".xcworkspace"})

SCREENSHOT_PREFIXES: tuple[str, ...] = (
    "screenshot",
    "screen shot",
    "cleanshot",
    "scr-",
    "captura de pantalla",
    "captura",
)

CAMERA_PREFIXES: tuple[str, ...] = (
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

IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {
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
)
PDF_EXTENSIONS: frozenset[str] = frozenset({"pdf"})
DOC_EXTENSIONS: frozenset[str] = frozenset(
    {
        "txt",
        "text",
        "md",
        "markdown",
        "rst",
        "rtf",
        "docx",
        "odt",
        "html",
        "htm",
    }
)
CODE_DOC_EXTENSIONS: frozenset[str] = frozenset({"md", "markdown", "rst"})
AUDIO_EXTENSIONS: frozenset[str] = frozenset({"mp3", "m4a", "wav", "aac", "flac", "ogg", "wma", "aiff"})
VIDEO_EXTENSIONS: frozenset[str] = frozenset({"mp4", "mov", "mkv", "avi", "webm", "m4v", "wmv"})
CODE_EXTENSIONS: frozenset[str] = frozenset(
    {
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
)
CODE_FILENAMES: frozenset[str] = frozenset(
    {
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
)

DATA_EXTENSIONS: frozenset[str] = frozenset(
    {
        "json",
        "jsonl",
        "xml",
        "toml",
        "plist",
        "csv",
        "tsv",
        "lock",
    }
)

_DATASET_MIN_COUNT = 300
_DATASET_MACHINE_RATIO = 0.7

_EXT_KIND_MAP: dict[str, FileKind] = {
    **dict.fromkeys(IMAGE_EXTENSIONS, FileKind.IMAGE),
    **dict.fromkeys(PDF_EXTENSIONS, FileKind.PDF),
    **dict.fromkeys(DOC_EXTENSIONS, FileKind.DOC),
    **dict.fromkeys(AUDIO_EXTENSIONS, FileKind.AUDIO),
    **dict.fromkeys(VIDEO_EXTENSIONS, FileKind.VIDEO),
    **dict.fromkeys(CODE_EXTENSIONS, FileKind.CODE),
}


def normalize_excluded_folders(excluded_folders: Iterable[str] | None) -> frozenset[str]:
    """Normalize excluded folder entries into a deduplicated frozenset."""
    if not excluded_folders:
        return frozenset()
    return frozenset(cleaned for entry in excluded_folders if (cleaned := entry.strip()))


def normalize_excluded_types(excluded_types: Iterable[str] | None) -> frozenset[str]:
    """Normalize excluded extension/kind names into lowercase frozenset without leading dots."""
    if not excluded_types:
        return frozenset()
    return frozenset(cleaned for entry in excluded_types if (cleaned := entry.lstrip(".").strip().lower()))


def is_secret(path: str) -> bool:
    """Return True if path represents a secret, private key, credential, or sensitive config directory."""
    p = Path(path)
    if any(part.lower() in SECRET_FOLDER_NAMES for part in p.parts[:-1]):
        return True
    ext = p.suffix.lstrip(".").lower()
    if ext in SECRET_EXTENSIONS:
        return True
    name = p.name.lower()
    if name in SECRET_EXACT_NAMES:
        return True
    if any(name.startswith(prefix) for prefix in SECRET_PREFIXES):
        return True
    return any(token in name for token in SECRET_SUBSTRINGS)


def is_minified_file(path: str) -> bool:
    """Return True if filename is a minified or bundled web asset."""
    return Path(path).name.lower().endswith(MINIFIED_SUFFIXES)


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
        if len(kind_stems) >= _DATASET_MIN_COUNT:
            machine_count = sum(1 for stem in kind_stems if looks_machine_named(stem))
            if (machine_count / len(kind_stems)) >= _DATASET_MACHINE_RATIO:
                return True
    return False


def classify_file(path: str, *, is_code_mode: bool = False) -> FileKind | None:
    """Map a file path to its FileKind classification, or None if unsupported."""
    p = Path(path)
    ext = p.suffix.lstrip(".").lower()
    name = p.name.lower()

    if name in CODE_FILENAMES or name.startswith("dockerfile"):
        return FileKind.CODE

    if is_code_mode and ext in CODE_DOC_EXTENSIONS:
        return FileKind.CODE

    kind = _EXT_KIND_MAP.get(ext)
    if kind == FileKind.IMAGE and any(name.startswith(prefix) for prefix in SCREENSHOT_PREFIXES):
        return FileKind.SCREENSHOT
    return kind


def is_path_excluded(path: str, excluded_folders: Iterable[str] | None) -> bool:
    """Return True if path is inside any user-configured excluded folder."""
    folders = (
        excluded_folders if isinstance(excluded_folders, frozenset) else normalize_excluded_folders(excluded_folders)
    )
    if not folders:
        return False
    p = Path(path)
    resolved_path = p.resolve() if p.is_absolute() else p
    parts_set = set(p.parts)
    for clean in folders:
        if clean.startswith(("/", "~")):
            excl_path = Path(clean).expanduser().resolve()
            if resolved_path.is_relative_to(excl_path):
                return True
        elif clean in parts_set:
            return True
    return False


def _is_excluded_type(ext: str, kind: FileKind, excluded_types: frozenset[str]) -> bool:
    if not excluded_types:
        return False
    return ext in excluded_types or kind.value in excluded_types


def _check_mode_compatibility(kind: FileKind, *, is_code_mode: bool, in_code_project: bool) -> str:
    if is_code_mode:
        return "not a code file" if kind != FileKind.CODE else ""
    if kind == FileKind.CODE:
        return "code file in general mode"
    if kind != FileKind.SCREENSHOT and in_code_project:
        return "non-screenshot inside code project"
    return ""


def _check_path_exclusions(path: str, p: Path, excluded_folders: frozenset[str]) -> str:
    for part in p.parts[:-1]:
        if part in SKIPPED_FOLDER_NAMES or part.endswith(".noindex"):
            return f"inside skipped folder {part}"
    if is_path_excluded(path, excluded_folders):
        return "inside excluded folder"
    if is_secret(path):
        return "secret or credential file"
    return ""


def should_skip(
    path: str,
    *,
    is_code_mode: bool,
    excluded_folders: Iterable[str] | None = None,
    excluded_types: Iterable[str] | None = None,
    in_code_project: bool = False,
) -> tuple[bool, str]:
    """Determine whether to skip a file during indexing."""
    norm_folders = (
        excluded_folders if isinstance(excluded_folders, frozenset) else normalize_excluded_folders(excluded_folders)
    )
    norm_types = excluded_types if isinstance(excluded_types, frozenset) else normalize_excluded_types(excluded_types)

    p = Path(path)
    path_reason = _check_path_exclusions(path, p, norm_folders)
    if path_reason:
        return True, path_reason

    if is_minified_file(path):
        return True, "minified or bundled asset"

    ext = p.suffix.lstrip(".").lower()
    name = p.name.lower()
    if is_code_mode and (ext in DATA_EXTENSIONS or name.endswith(".lock") or "-lock." in name):
        return True, "data format in code mode"

    kind = classify_file(path, is_code_mode=is_code_mode)
    if kind is None:
        return True, "unsupported file kind"

    if _is_excluded_type(ext, kind, norm_types):
        return True, f"excluded file type {ext or kind.value}"

    mode_reason = _check_mode_compatibility(kind, is_code_mode=is_code_mode, in_code_project=in_code_project)
    return (True, mode_reason) if mode_reason else (False, "")
