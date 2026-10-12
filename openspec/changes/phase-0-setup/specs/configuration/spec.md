# Spec Delta

## Purpose

The configuration capability manages SemSearch application settings, persistence to disk, folder inclusion/exclusion lists, code search preferences, model endpoints, and indexing schedules.

## ADDED Requirements

### Requirement: Configuration Persistence and Management
The application MUST provide an `AppConfig` data model and a `ConfigManager` that reads, updates, and writes configuration to `~/.config/semsearch/config.json` with standard defaults.

#### Scenario: Load default configuration when file does not exist
- **WHEN** `ConfigManager.load()` is invoked and the config file does not exist
- **THEN** default configuration is returned with expected defaults (model 'embeddinggemma-2', ollama_url 'http://localhost:11434', code_editor 'auto')

#### Scenario: Add and remove folder paths
- **WHEN** `add_folder(path)` or `remove_folder(path)` is called on `ConfigManager`
- **THEN** the folders list is updated, deduplicated, and persisted to disk
