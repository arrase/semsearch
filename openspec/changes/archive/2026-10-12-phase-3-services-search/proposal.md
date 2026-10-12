# Proposal

## Why

SemSearch needs a unified query and background indexing runtime to connect its core database, vector math, and multimodal extractors into a working desktop search system. By combining full-text BM25 search with vector similarity via DigUp hybrid ranking, pipelining background indexing queues, and integrating native Linux filesystem watchers, power policies, and desktop deep links, SemSearch delivers fast, accurate search results and seamless desktop navigation with minimal system resource consumption.

## What Changes

- Implement `src/semsearch/services/searcher.py` providing dual query mode (`code:` routing vs general index), FTS5 BM25 keyword matching with query normalization and coverage scoring, vector cosine similarity via Ollama embeddings, cross-modality Z-score fusion, lookup query detection, and DigUp hybrid ranking with quality gating and weaker matches folding.
- Implement `src/semsearch/services/indexer.py` providing `IndexService` with a two-stage pipelined worker (background extraction/embedding + main commit), multi-tier priority queues (immediate new files, full backfill, code index, long file continuation), dynamic `MeasuredCosts` throughput tracking, time/disk estimation, and `EngineStatus` lifecycle state management.
- Implement `src/semsearch/services/watcher.py` using Linux inotify (`watchdog`) with differential debouncing: 0.25s event latency with 0.75s debounce window for general folders, and 2.0s latency with 60s quiet debounce window (and 10-minute maximum wait ceiling) for code repositories.
- Implement `src/semsearch/services/scheduler.py` inspecting native Linux power supplies via `/sys/class/power_supply/` for battery pause policy, and enforcing configurable operating hours schedules with overnight window support.
- Implement `src/semsearch/services/actions.py` executing Linux desktop actions including default application launching (`xdg-open`), media playback timestamp jumps (`mpv`, `vlc`), PDF document page jumps (`okular`, `evince`), code editor line jumps (VS Code, Zed, Cursor, Sublime, JetBrains), file manager selection reveal (DBus `org.freedesktop.FileManager1` and CLI fallbacks), and clipboard copying.
- Add comprehensive unit and integration test suites for search ranking, indexing pipelines, filesystem watchers, scheduler power checks, and desktop actions.

## Capabilities

### New Capabilities
- `search-engine`: Hybrid ranking, dual query routing, FTS5 BM25 and vector fusion, gating, and weaker matches folding.
- `indexing-service`: Pipelined two-stage indexing worker, multi-tier priority queues, performance measurement, time/space estimation, and status reporting.
- `desktop-integration`: Linux inotify filesystem watching via watchdog, battery/schedule power management, and desktop deep links.

### Modified Capabilities
None.

## Impact

- Introduces services in `src/semsearch/services/`: `searcher.py`, `indexer.py`, `watcher.py`, `scheduler.py`, and `actions.py`.
- Integrates existing `Database`, `OllamaClient`, multimodal extractors, `Crawler`, and vector math into active background services.
- Depends on `watchdog` for inotify file monitoring and standard Linux desktop utilities (`xdg-open`, DBus, `/sys/class/power_supply/`).
- Downstream GUI interfaces (PySide6) in Phase 4 will consume `SearchService`, `IndexService`, `EngineStatus`, and desktop actions directly.
