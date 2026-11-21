"""Configuration models and manager for SemSearch."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "semsearch" / "config.json"


def _normalize_folder(folder: str) -> str:
    """Resolve and normalize a folder path."""
    return str(Path(folder).expanduser().resolve())


def _is_valid_field_value(default: object, value: object) -> bool:
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


class ConfigManager:
    """Manages reading and persisting SemSearch configuration."""

    def __init__(self, config_path: Path = DEFAULT_CONFIG_PATH) -> None:
        self.config_path = config_path
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
            if isinstance(k, str) and k in defaults and _is_valid_field_value(defaults[k], v)
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
