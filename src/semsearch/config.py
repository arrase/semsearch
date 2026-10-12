"""Configuration models and manager for SemSearch."""

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "semsearch" / "config.json"


@dataclass
class AppConfig:
    """SemSearch application settings."""

    folders: list[str] = field(default_factory=list)
    excluded_folders: list[str] = field(default_factory=list)
    excluded_types: list[str] = field(default_factory=lambda: ["heic", "txt"])
    code_enabled: bool = False
    code_folders: list[str] = field(default_factory=list)
    code_editor: str = "auto"
    ollama_url: str = "http://localhost:11434"
    model: str = "embeddinggemma-2"
    hotkey: str = "ctrl+shift+space"
    index_on_battery: bool = True
    index_schedule_enabled: bool = False
    index_schedule_start: int = 1320
    index_schedule_end: int = 420
    onboarded: bool = False


class ConfigManager:
    """Manages reading, updating, and persisting SemSearch configuration."""

    def __init__(self, config_path: Path | str | None = None) -> None:
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self.config: AppConfig = self.load()

    def load(self) -> AppConfig:
        """Load configuration from disk or return defaults if file does not exist."""
        if not self.config_path.is_file():
            return AppConfig()
        content = self.config_path.read_text(encoding="utf-8")
        data = json.loads(content)
        valid_fields = {f for f in AppConfig.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return AppConfig(**filtered)

    def save(self, config: AppConfig | None = None) -> None:
        """Persist current configuration to disk."""
        if config is not None:
            self.config = config
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(self.config), indent=2)
        self.config_path.write_text(payload, encoding="utf-8")

    def add_folder(self, folder: str) -> None:
        """Add a folder to searched paths and save."""
        resolved = str(Path(folder).expanduser().resolve())
        if resolved not in self.config.folders:
            self.config.folders.append(resolved)
            self.save()

    def remove_folder(self, folder: str) -> None:
        """Remove a folder from searched paths and save."""
        resolved = str(Path(folder).expanduser().resolve())
        self.config.folders = [f for f in self.config.folders if f != resolved and f != folder]
        self.save()

    def update(self, **kwargs) -> AppConfig:
        """Update configuration fields and save."""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        self.save()
        return self.config
