# Proposal

## Why

SemSearch requires foundational project infrastructure, package configuration, configuration management, and core data types to port DigUp to Linux as an elegant, KISS-compliant desktop semantic search engine.

## What Changes

- Initialize Python packaging with `pyproject.toml` using `hatchling` build backend and project dependencies (`pyside6`, `httpx`, `numpy`, `pymupdf`, `pillow`, `watchdog`, `pynput`, `python-docx`, `pytest`).
- Establish the modular directory structure under `src/semsearch`.
- Implement `AppConfig` and `ConfigManager` in `src/semsearch/config.py` for persistent JSON configuration at `~/.config/semsearch/config.json`.
- Implement DigUp-compatible data contracts and enums in `src/semsearch/core/types.py`.
- Provide automated unit testing in `tests/test_config.py`.

## Capabilities

### New Capabilities
- `configuration`: Manage SemSearch user configuration file, defaults, and folder management methods.
- `core-types`: Provide core data models, enums, records, and search hit structures mirroring DigUp.

### Modified Capabilities

None.

## Impact

- Creates the root packaging configuration and virtual environment.
- Establishes foundational data types consumed by extractors, indexing, and GUI layers.
