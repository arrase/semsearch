# Design

## Context

SemSearch is a Linux port of DigUp focused on simplicity, elegance, and clarity (KISS principle). It uses local Ollama multimodal embeddings (specifically `embeddinggemma-2`) and local file system indexing. Phase 0 sets up packaging, types, and configuration.

## Goals / Non-Goals

**Goals:**
- Set up standard PEP 517/621 packaging with `pyproject.toml` and `hatchling`.
- Scaffolding of package directories adhering to strict PEP 8 top-level imports and zero bloat.
- Simple, robust JSON-backed configuration manager in `src/semsearch/config.py`.
- Clean dataclass-based core domain types in `src/semsearch/core/types.py`.
- Unit test suite for configuration and type verification.

**Non-Goals:**
- Embedding extraction or pipeline implementation (handled in later phases).
- GUI widget rendering or global hotkey registration (handled in subsequent phases).
- Any OCR processing or dependencies (explicitly forbidden by design).

## Decisions

- **Build Backend**: Use `hatchling` in `pyproject.toml` for standard lightweight builds.
- **Data Models**: Use standard Python `dataclasses` with type hints for domain models.
- **Config Storage**: `~/.config/semsearch/config.json` formatted as JSON, managed via `pathlib.Path`.
- **Top-level Imports**: Enforce strict top-level imports across all modules with zero function-level imports.

## Risks / Trade-offs

- Simplicity over complex validation: Keep configuration validation straightforward without bloated validation frameworks.
