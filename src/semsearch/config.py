"""Configuration models and manager for SemSearch."""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "semsearch" / "config.json"

_ALLOWED_STR_VALUES: dict[str, frozenset[str]] = {
    "ollama_keep_alive": frozenset({"unload_idle", "server", "keep_loaded"}),
    "theme": frozenset({"dark", "light", "system"}),
}
_MIN_INT_VALUES: dict[str, int] = {
    "ollama_timeout": 5,
    "max_media_duration_min": 0,
    "code_watch_delay_sec": 1,
}


def resolve_xdg_config_home() -> Path:
    """Return XDG_CONFIG_HOME if set, otherwise ~/.config."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return Path(xdg) if xdg else Path.home() / ".config"


def resolve_xdg_data_home() -> Path:
    """Return XDG_DATA_HOME if set, otherwise ~/.local/share."""
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    return Path(xdg) if xdg else Path.home() / ".local" / "share"


def resolve_xdg_cache_home() -> Path:
    """Return XDG_CACHE_HOME if set, otherwise ~/.cache."""
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    return Path(xdg) if xdg else Path.home() / ".cache"


def get_default_config_path() -> Path:
    """Return default XDG path for SemSearch config.json."""
    return resolve_xdg_config_home() / "semsearch" / "config.json"


def get_autostart_path() -> Path:
    """Return default XDG autostart desktop entry path."""
    return resolve_xdg_config_home() / "autostart" / "semsearch.desktop"


def get_thumbs_cache_dir() -> Path:
    """Return default XDG cache directory for preview thumbnails."""
    return resolve_xdg_cache_home() / "semsearch" / "thumbs"


def _normalize_folder(folder: str) -> str:
    """Resolve and normalize a folder path."""
    return str(Path(folder).expanduser().resolve())


def _is_valid_type(default: object, value: object) -> bool:
    """Return True if a deserialized JSON value matches the expected AppConfig field type."""
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default, str):
        return isinstance(value, str)
    if isinstance(default, list):
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    return False


def _is_valid_field_value(key: str, default: object, value: object) -> bool:
    """Return True if a deserialized JSON value matches expected type and domain bounds."""
    if not _is_valid_type(default, value):
        return False
    if isinstance(value, int) and key in _MIN_INT_VALUES:
        return value >= _MIN_INT_VALUES[key]
    if isinstance(value, str) and key in _ALLOWED_STR_VALUES:
        return value in _ALLOWED_STR_VALUES[key]
    return True


@dataclass
class AppConfig:
    """SemSearch application settings."""

    folders: list[str] = field(default_factory=list)
    excluded_folders: list[str] = field(default_factory=list)
    excluded_types: list[str] = field(default_factory=lambda: ["heic"])
    code_enabled: bool = False

    code_folders: list[str] = field(default_factory=list)
    code_editor: str = "auto"
    ollama_url: str = "http://localhost:11434"
    model: str = "embeddinggemma-2"
    num_ctx: int = 0
    hotkey: str = "ctrl+shift+space"
    language: str = "auto"
    index_on_battery: bool = True
    index_schedule_enabled: bool = False
    index_schedule_start: int = 1320
    index_schedule_end: int = 420
    onboarded: bool = False
    data_dir: str = ""
    follow_symlinks: bool = False
    skip_code_projects: bool = True
    skip_datasets: bool = True
    max_media_duration_min: int = 60
    code_watch_delay_sec: int = 60
    ollama_keep_alive: str = "unload_idle"
    ollama_timeout: int = 120
    ollama_api_key: str = ""
    custom_editor_cmd: str = ""
    terminal: str = "auto"
    custom_terminal_cmd: str = ""
    theme: str = "dark"
    close_on_focus_loss: bool = True
    remember_last_query: bool = False


def resolve_data_dir(config: AppConfig) -> Path:
    """Return configured data directory if set, otherwise default XDG data directory."""
    custom = config.data_dir.strip()
    if custom:
        return Path(custom).expanduser().resolve()
    return resolve_xdg_data_home() / "semsearch"


def _validate_config_path(config_path: Path) -> Path:
    """Canonicalize config_path and verify it stays inside allowed user directories."""
    resolved = os.path.realpath(config_path.expanduser())
    allowed_roots = (
        os.path.realpath(Path.home()),
        os.path.realpath(resolve_xdg_config_home()),
        os.path.realpath(Path.cwd()),
        os.path.realpath(tempfile.gettempdir()),
    )
    for root in allowed_roots:
        prefix = root if root.endswith(os.sep) else root + os.sep
        if resolved.startswith(prefix):
            return Path(resolved)
    raise ValueError(f"Config path outside allowed directories: {config_path}")


class ConfigManager:
    """Manages reading and persisting SemSearch configuration."""

    def __init__(self, config_path: Path | None = None) -> None:
        raw_path = config_path if config_path is not None else get_default_config_path()
        self.config_path = _validate_config_path(raw_path)
        self.config: AppConfig = self.load()

    def load(self) -> AppConfig:
        """Load configuration from disk or return defaults if missing or corrupt."""
        if not self.config_path.is_file():
            return AppConfig()
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return AppConfig()
        if not isinstance(data, dict):
            return AppConfig()
        defaults = asdict(AppConfig())
        filtered = {
            k: v
            for k, v in data.items()
            if isinstance(k, str) and k in defaults and _is_valid_field_value(k, defaults[k], v)
        }
        return AppConfig(**filtered)

    def save(self) -> None:
        """Atomically persist current configuration to disk with owner-only permissions."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(self.config), indent=2)
        tmp_path = self.config_path.with_name(f".{self.config_path.name}.tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.chmod(0o600)
        tmp_path.replace(self.config_path)

    def add_folder(self, folder: str, *, is_code: bool = False) -> bool:
        """Normalize and add a folder if not already present, persisting on change."""
        normalized = _normalize_folder(folder)
        target = self.config.code_folders if is_code else self.config.folders
        if any(_normalize_folder(existing) == normalized for existing in target):
            return False
        target.append(normalized)
        self.save()
        return True

    def remove_folder(self, folder: str, *, is_code: bool = False) -> bool:
        """Normalize and remove a folder if present, persisting on change."""
        normalized = _normalize_folder(folder)
        target = self.config.code_folders if is_code else self.config.folders
        remaining = [existing for existing in target if _normalize_folder(existing) != normalized]
        if len(remaining) == len(target):
            return False
        target[:] = remaining
        self.save()
        return True
