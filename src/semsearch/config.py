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
    excluded_types: list[str] = field(default_factory=lambda: ["heic"])
    code_enabled: bool = False

    code_folders: list[str] = field(default_factory=list)
    code_editor: str = "auto"
    ollama_url: str = "http://localhost:11434"
    model: str = "embeddinggemma-2"
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
        """Load configuration from disk or return defaults if file does not exist."""
        if not self.config_path.is_file():
            return AppConfig()
        data = json.loads(self.config_path.read_text(encoding="utf-8"))
        valid_fields = set(AppConfig.__dataclass_fields__)
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return AppConfig(**filtered)

    def save(self) -> None:
        """Persist current configuration to disk."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(self.config), indent=2)
        self.config_path.write_text(payload, encoding="utf-8")
