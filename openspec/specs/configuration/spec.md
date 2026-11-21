# configuration Specification

## Purpose
The configuration capability manages SemSearch application settings, atomic owner-private persistence to disk, resilient loading against corrupted configuration files, folder inclusion/exclusion lists, code search preferences, model endpoints, indexing schedules, and XDG data directory resolution.

## Requirements

### Requirement: Configuration Model and XDG Storage Paths
The application MUST provide an `AppConfig` data model and a `ConfigManager` that reads, updates, and writes configuration to `~/.config/semsearch/config.json` with standard defaults (`model = "embeddinggemma-2"`, `ollama_url = "http://localhost:11434"`, `num_ctx = 0`, `code_editor = "auto"`, `excluded_types = ["heic"]`, `hotkey = "ctrl+shift+space"`, `language = "auto"`, `index_on_battery = True`, `index_schedule_enabled = False`, `index_schedule_start = 1320`, `index_schedule_end = 420`, `onboarded = False`). Database files MUST be stored under `$XDG_DATA_HOME/semsearch` (falling back to `~/.local/share/semsearch`).

#### Scenario: Load default configuration when file does not exist
- **WHEN** `ConfigManager.load()` is invoked and `~/.config/semsearch/config.json` does not exist
- **THEN** default configuration is returned with expected defaults (`model = "embeddinggemma-2"`, `ollama_url = "http://localhost:11434"`, `excluded_types = ["heic"]`, `code_editor = "auto"`)

#### Scenario: Resolve XDG data directory
- **WHEN** `XDG_DATA_HOME` is set in the environment
- **THEN** SemSearch resolves its index database directory to `$XDG_DATA_HOME/semsearch` instead of hardcoding `~/.local/share/semsearch`

### Requirement: Atomic Owner-Private Persistence
`ConfigManager.save()` MUST persist configuration atomically by writing JSON to a sibling temporary file (`.config.json.tmp`), applying owner-only permissions (`0o600`), and replacing the target file via `Path.replace()` so interrupted writes never corrupt `config.json` and local configuration remains unreadable to other users.

#### Scenario: Atomic configuration save with 0o600 permissions
- **WHEN** `ConfigManager.save()` writes updated settings to disk
- **THEN** parent directories are created if absent, the JSON payload is written to a temporary sibling file with `0o600` permissions, and it atomically replaces `config.json`

### Requirement: Resilient Loading of Corrupt or Partial JSON
`ConfigManager.load()` MUST handle corrupted, truncated, non-UTF-8, or non-object JSON files gracefully by returning default `AppConfig()` values without crashing, and MUST ignore unknown keys or fields whose deserialized JSON type does not match the expected `AppConfig` field type.

#### Scenario: Recovering from corrupted JSON configuration
- **WHEN** `config.json` contains malformed JSON, invalid UTF-8 bytes, or a non-object JSON root
- **THEN** `ConfigManager.load()` returns a valid default `AppConfig()` instance without raising an exception

#### Scenario: Ignoring invalid field types and unknown keys
- **WHEN** `config.json` includes unrecognized keys or mismatched field types (such as a string for `num_ctx` or a boolean for an integer field)
- **THEN** valid fields are loaded and invalid or unknown entries fall back cleanly to their `AppConfig` defaults

### Requirement: Normalized Folder Management
`ConfigManager` MUST provide `add_folder(folder, *, is_code=False) -> bool` and `remove_folder(folder, *, is_code=False) -> bool` methods that expand and resolve paths (`Path(folder).expanduser().resolve()`), deduplicate against existing entries in `folders` or `code_folders`, persist changes atomically via `save()`, and return whether the list was modified.

#### Scenario: Add and remove folder paths with deduplication
- **WHEN** `add_folder(path)` or `remove_folder(path)` is called on `ConfigManager` (including with `~` or relative variants of an existing path)
- **THEN** the target list (`folders` or `code_folders`) is updated, deduplicated against normalized paths, persisted via `save()` only when changed, and returns `True` on modification or `False` on no-op
