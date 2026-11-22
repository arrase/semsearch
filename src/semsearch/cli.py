"""Lightweight headless CLI operations and user-space desktop integration for SemSearch."""

import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from semsearch.config import ConfigManager, resolve_data_dir, resolve_xdg_data_home
from semsearch.core.database import CODE_DB_FILE, MAIN_DB_FILE, Database
from semsearch.core.ollama import OllamaClient
from semsearch.core.types import FileKind, SearchHit
from semsearch.services.indexer import IndexService
from semsearch.services.searcher import SearchService

_VALID_KINDS: dict[str, FileKind] = {kind.value: kind for kind in FileKind}
_BYTES_PER_UNIT = 1024.0
_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB")
_ICON_SOURCE_PATH = Path(__file__).parent / "gui" / "icons" / "semsearch.svg"


def get_default_data_dir() -> Path:
    """Return default XDG data directory for SemSearch databases."""
    return resolve_xdg_data_home() / "semsearch"


def hit_to_dict(hit: SearchHit) -> dict[str, object]:
    """Serialize a SearchHit into a JSON-compatible dictionary."""
    return {
        "path": hit.path,
        "kind": hit.kind.value,
        "score": round(hit.score, 4),
        "z": hit.z,
        "cosine": hit.cosine,
        "keyword_rank": hit.keyword_rank,
        "segment": hit.segment.value,
        "loc": hit.loc,
        "loc_end": hit.loc_end,
        "focus": hit.focus,
        "excerpt": hit.excerpt,
        "reason": str(hit.reason),
        "modified": hit.modified,
        "size": hit.size,
    }


def _parse_kinds(kinds: list[str] | None) -> tuple[set[FileKind] | None, list[str]]:
    if not kinds:
        return None, []
    parsed: set[FileKind] = set()
    invalid: list[str] = []
    for entry in kinds:
        for raw in entry.split(","):
            token = raw.strip().lower()
            if not token:
                continue
            matched = _VALID_KINDS.get(token)
            if matched is None:
                invalid.append(token)
            else:
                parsed.add(matched)
    return (parsed or None), invalid


def _format_loc_suffix(hit: SearchHit) -> str:
    if hit.kind == FileKind.CODE and (hit.focus > 0 or hit.loc > 0):
        line = int(hit.focus) if hit.focus > 0 else int(hit.loc)
        return f":{line}"
    if hit.kind == FileKind.PDF and hit.loc > 0:
        return f" (p.{int(hit.loc)})"
    if hit.kind in (FileKind.AUDIO, FileKind.VIDEO) and hit.loc > 0:
        mins, secs = divmod(int(hit.loc), 60)
        return f" ({mins:02d}:{secs:02d})"
    return ""


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in _SIZE_UNITS[:-1]:
        if value < _BYTES_PER_UNIT:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= _BYTES_PER_UNIT
    return f"{value:.1f} {_SIZE_UNITS[-1]}"


def _db_status(db: Database) -> dict[str, object]:
    stats = db.stats()
    db_path = Path(db.path)
    dim_raw = db.get_meta("embedding_dim")
    return {
        "path": db.path,
        "files": stats["files"],
        "pending": stats["pending"],
        "failed": stats["failed"],
        "segments": stats["segments"],
        "vectors": stats["vectors"],
        "embedding_model": db.get_meta("embedding_model"),
        "embedding_dim": int(dim_raw) if dim_raw and dim_raw.isdigit() else None,
        "disk_bytes": db_path.stat().st_size if db_path.is_file() else 0,
    }


def _format_db_summary(label: str, info: dict[str, object]) -> str:
    model = info["embedding_model"] or "-"
    dim = info["embedding_dim"] if info["embedding_dim"] is not None else "-"
    disk_bytes = info["disk_bytes"]
    size_str = _format_bytes(disk_bytes if isinstance(disk_bytes, int) else 0)
    return (
        f"{label}: {info['files']} files ({info['pending']} pending, {info['failed']} failed), "
        f"{info['segments']} segments, {info['vectors']} vectors, {size_str}, model={model}, dim={dim}\n"
    )


def run_query(
    query: str,
    *,
    is_code: bool = False,
    kinds: list[str] | None = None,
    limit: int = 20,
    as_json: bool = False,
    config_manager: ConfigManager | None = None,
    data_dir: Path | None = None,
    ollama_client: OllamaClient | None = None,
) -> int:
    """Execute a search query from the CLI and write JSON or formatted lines to stdout."""
    kind_set, invalid = _parse_kinds(kinds)
    if invalid:
        valid_names = ", ".join(_VALID_KINDS)
        sys.stderr.write(f"Invalid kind(s): {', '.join(invalid)}. Valid kinds: {valid_names}\n")
        return 2

    cfg = (config_manager or ConfigManager()).config
    base_dir = data_dir or resolve_data_dir(cfg)
    db = Database(base_dir / MAIN_DB_FILE)
    code_db = Database(base_dir / CODE_DB_FILE) if cfg.code_enabled else None
    owns_ollama = ollama_client is None
    ollama = (
        ollama_client
        if ollama_client is not None
        else OllamaClient(
            base_url=cfg.ollama_url,
            embed_timeout=float(cfg.ollama_timeout),
            num_ctx=cfg.num_ctx,
            api_key=cfg.ollama_api_key,
            keep_alive=cfg.ollama_keep_alive,
        )
    )

    try:
        service = SearchService(db, code_db=code_db, ollama=ollama, model=cfg.model)
        results = service.search(query, limit=limit, is_code=is_code, kinds=kind_set)
        if as_json:
            payload = [hit_to_dict(hit) for hit in results.ranked]
            sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        else:
            for hit in results.ranked:
                loc_suffix = _format_loc_suffix(hit)
                sys.stdout.write(f"{hit.kind.value:10s} {hit.score:5.2f}  {hit.path}{loc_suffix}\n")
        return 0
    finally:
        if owns_ollama:
            ollama.close()
        if code_db is not None:
            code_db.close()
        db.close()


def run_status(
    *,
    as_json: bool = False,
    config_manager: ConfigManager | None = None,
    data_dir: Path | None = None,
) -> int:
    """Inspect index statistics, model metadata, and disk usage and print to stdout."""
    cfg = (config_manager or ConfigManager()).config
    base_dir = data_dir or resolve_data_dir(cfg)
    main_path = base_dir / MAIN_DB_FILE
    code_path = base_dir / CODE_DB_FILE

    db = Database(main_path)
    code_db = Database(code_path) if (cfg.code_enabled or code_path.is_file()) else None

    try:
        main_info = _db_status(db)
        code_info = _db_status(code_db) if code_db is not None else None
        payload: dict[str, object] = {
            "data_dir": str(base_dir),
            "configured_model": cfg.model,
            "ollama_url": cfg.ollama_url,
            "code_enabled": cfg.code_enabled,
            "main": main_info,
            "code": code_info,
        }
        if as_json:
            sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        else:
            sys.stdout.write(f"Data dir: {base_dir}\n")
            sys.stdout.write(f"Configured model: {cfg.model} ({cfg.ollama_url})\n")
            sys.stdout.write(_format_db_summary("Main index", main_info))
            if code_info is not None:
                sys.stdout.write(_format_db_summary("Code index", code_info))
        return 0
    finally:
        if code_db is not None:
            code_db.close()
        db.close()


def run_reindex(
    *,
    force: bool = False,
    forward_ipc: Callable[[str], bool] | None = None,
    config_manager: ConfigManager | None = None,
    data_dir: Path | None = None,
    ollama_client: OllamaClient | None = None,
) -> int:
    """Trigger re-indexing via running daemon IPC or perform a standalone crawl and sync."""
    cmd = "REINDEX_FORCE" if force else "REINDEX"
    if forward_ipc is not None and forward_ipc(cmd):
        sys.stdout.write(f"Sent {cmd} to running SemSearch daemon.\n")
        return 0

    cfg = (config_manager or ConfigManager()).config
    base_dir = data_dir or resolve_data_dir(cfg)
    db = Database(base_dir / MAIN_DB_FILE)
    code_db = Database(base_dir / CODE_DB_FILE) if cfg.code_enabled else None
    owns_ollama = ollama_client is None
    ollama = (
        ollama_client
        if ollama_client is not None
        else OllamaClient(
            base_url=cfg.ollama_url,
            embed_timeout=float(cfg.ollama_timeout),
            num_ctx=cfg.num_ctx,
            api_key=cfg.ollama_api_key,
            keep_alive=cfg.ollama_keep_alive,
        )
    )

    try:
        index_service = IndexService(db, code_db=code_db, ollama=ollama, config=cfg)
        reset_count = index_service.reset_all_for_reindex() if force else 0
        index_service.crawl_and_sync(is_code=False)
        if cfg.code_enabled and code_db is not None:
            index_service.crawl_and_sync(is_code=True)
        status = index_service.get_status()
        if force:
            sys.stdout.write(f"Reset {reset_count} files for full re-index.\n")
        sys.stdout.write(f"Synced index: {status.files} files ({status.pending} pending, {status.vectors} vectors).\n")
        return 0
    finally:
        if owns_ollama:
            ollama.close()
        if code_db is not None:
            code_db.close()
        db.close()


def install_desktop_integration(
    *,
    data_home: Path | None = None,
    exec_path: str | None = None,
) -> list[Path]:
    """Install user-space desktop launcher, SVG icon, D-Bus service, KRunner, and GNOME search provider."""
    base = data_home if data_home is not None else resolve_xdg_data_home()
    binary = exec_path or shutil.which("semsearch") or sys.argv[0]

    desktop_file = base / "applications" / "semsearch.desktop"
    icon_file = base / "icons" / "hicolor" / "scalable" / "apps" / "semsearch.svg"
    dbus_service_file = base / "dbus-1" / "services" / "org.semsearch.DesktopSearch.service"
    krunner_file = base / "krunner" / "dbusplugins" / "semsearch.desktop"
    gnome_file = base / "gnome-shell" / "search-providers" / "semsearch-search-provider.ini"

    text_files: list[tuple[Path, str]] = [
        (
            desktop_file,
            (
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=SemSearch\n"
                "Comment=Linux desktop semantic search engine\n"
                f'Exec="{binary}" --toggle\n'
                "Icon=semsearch\n"
                "Terminal=false\n"
                "Categories=Utility;Search;\n"
                "StartupWMClass=semsearch\n"
                "Actions=Settings;\n"
                "\n"
                "[Desktop Action Settings]\n"
                "Name=Settings\n"
                f'Exec="{binary}" --settings\n'
            ),
        ),
        (
            dbus_service_file,
            (f'[D-BUS Service]\nName=org.semsearch.DesktopSearch\nExec="{binary}" --daemon\n'),
        ),
        (
            krunner_file,
            (
                "[Desktop Entry]\n"
                "Type=Service\n"
                "Name=SemSearch\n"
                "Comment=Semantic desktop search powered by SemSearch\n"
                "Icon=semsearch\n"
                "X-Plasma-API=DBus\n"
                "X-Plasma-DBusRunner-Service=org.semsearch.DesktopSearch\n"
                "X-Plasma-DBusRunner-Path=/krunner\n"
                "X-Plasma-Request-Actions-Once=true\n"
            ),
        ),
        (
            gnome_file,
            (
                "[Shell Search Provider]\n"
                "Desktop Id=semsearch.desktop\n"
                "BusName=org.semsearch.DesktopSearch\n"
                "ObjectPath=/org/semsearch/SearchProvider\n"
                "Version=2\n"
            ),
        ),
    ]

    for target, content in text_files:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    icon_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_ICON_SOURCE_PATH, icon_file)

    return [desktop_file, icon_file, dbus_service_file, krunner_file, gnome_file]


def run_install_desktop(
    *,
    data_home: Path | None = None,
    exec_path: str | None = None,
) -> int:
    """Install user-space desktop integration files and print installed paths to stdout."""
    created = install_desktop_integration(data_home=data_home, exec_path=exec_path)
    sys.stdout.write("Installed SemSearch desktop integration files:\n")
    for path in created:
        sys.stdout.write(f"  {path}\n")
    gnome_provider = created[-1]
    sys.stdout.write(
        "Note: On distributions where GNOME Shell only scans system search providers, create a symlink:\n"
        f"  sudo ln -sf {gnome_provider} /usr/share/gnome-shell/search-providers/{gnome_provider.name}\n"
    )
    return 0
