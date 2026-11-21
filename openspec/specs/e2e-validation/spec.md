# e2e-validation Specification

## Purpose
Provides comprehensive end-to-end integration testing across synthetic multimodal testbeds, WAL concurrency and resilience, hybrid BM25 + Z-score search fidelity with inline filters, headless CLI commands, IPC forwarding, native D-Bus search providers, and strict Zero-OCR architectural invariant enforcement.

## Requirements

### Requirement: Multimodal Testbed Generation and Zero-OCR Ingestion
The testbed SHALL generate valid synthetic sample files across all supported modalities without requiring external internet access: raster images, `.svg` vector graphics, desktop screenshots, digital and visual PDFs, formatted documents (`.txt`, `.md`, `.html`, `.rtf`, `.docx`, `.odt` — excluding legacy binary `.doc`), audio recordings with active and silent segments, video clips with distinct scenes, source code files with symbols, and Jupyter notebooks with cleared outputs. Visual ingestion SHALL strictly preserve the Zero-OCR invariant without importing or executing any OCR engine and without `SegmentKind.OCR`.

#### Scenario: Generating and ingesting synthetic multimodal testbed artifacts
- **WHEN** the synthetic testbed is generated and processed through the indexing pipeline
- **THEN** all supported files (including `.svg` rasterized via PyMuPDF) are extracted, L2-normalized, vectorized, and stored in schema v6 `index.sqlite` and `code.sqlite` with zero failed files and zero OCR calls

### Requirement: WAL Concurrency, Vector Preservation, and Ollama Outage Resilience
End-to-end validation SHALL verify that:
- Concurrent WAL readers execute searches without blocking during active write transactions and rolled-back transactions leave storage and `VectorSnapshot` caches intact.
- Moving or renaming an already indexed (`DONE`) file preserves its segments and float16 vectors without re-embedding.
- Temporary Ollama outages (`OllamaConnectionError`, `OllamaTimeoutError`) pause indexing with `waiting_for = "Ollama"` and exponential backoff without marking files `FAILED`, resuming cleanly once Ollama recovers.
- Changing the configured embedding model or vector dimension updates `meta` (`embedding_model`, `embedding_dim`) and triggers clean re-indexing.

#### Scenario: Surviving transient Ollama downtime and file moves
- **WHEN** Ollama fails transiently during indexing or an indexed file is moved (`mv`)
- **THEN** pending files remain `PENDING` and index automatically after Ollama recovers, while moved files retain existing vectors without re-embedding

### Requirement: Hybrid BM25 + Z-Score Fidelity, Inline Filters, and Quality Gating (`SEMANTIC_Z_GATE = 2.2`)
The search engine SHALL enforce `SEMANTIC_Z_GATE = 2.2` and lexical coverage `>= 0.5`, blend BM25 rank (`bm25_bonus`), coverage, and modality-aware Z-scores, fold hits scoring below `50%` of the top candidate score into `ResultSet.weaker`, evaluate inline filters (`code:`, `kind:`, `ext:`, `path:`, `after:`), cross-match `camelCase`/`snake_case` and CJK terms, and anchor code hits to symbol definitions.

#### Scenario: Quality gating at Z-score 2.2 and inline filter evaluation
- **WHEN** a query produces candidates with `z < 2.2` and `coverage < 0.5`, or includes inline filters such as `kind:pdf ext:pdf path:reports after:7d`
- **THEN** sub-threshold noise candidates are excluded from both `ranked` and `weaker`, hits below `50%` of the top score fold into `weaker`, and inline filters restrict results accurately (skipping Ollama when no clean query text remains)

#### Scenario: Cross-modal retrieval, serial lookup, and code definition anchoring
- **WHEN** descriptive queries match images/video frames, identifier lookups match serials, or `code:` queries match symbols (including `camelCase` and qualified `Class.method` identifiers)
- **THEN** visual hits rank via Z-score calibration, lookups boost lexical BM25 + coverage scores, and code hits carry exact definition line anchors (`focus`)

### Requirement: CLI Activation, Headless Subcommands, and Single-Instance IPC
End-to-end validation SHALL verify GUI activation flags (`--daemon`, `--panel`, `--toggle`, `--settings`), single-instance lockfile (`flock`) and `AF_UNIX` IPC command forwarding (`ipc.sock`), and headless subcommands (`semsearch query --json`, `semsearch status --json`, `semsearch reindex --force`, `semsearch install-desktop`).

#### Scenario: Running headless CLI subcommands and IPC toggle forwarding
- **WHEN** `semsearch query --json`, `semsearch status --json`, `semsearch reindex --force`, and `semsearch install-desktop` run headless, or a secondary process invokes `semsearch --toggle` / `--panel` / `--settings` while a daemon is active
- **THEN** headless commands output valid JSON/status and install user-space desktop files cleanly, while secondary GUI invocations forward over `ipc.sock` and exit with status `0`

#### Scenario: Default daemon launch anchored in system tray
- **WHEN** `semsearch` is invoked without flags or with `--daemon` on an onboarded installation with an available system tray
- **THEN** the application runs anchored in the system tray without opening the search panel until toggled

### Requirement: Native D-Bus Desktop Search Providers and Deep-Link Actions
End-to-end validation SHALL verify that `org.semsearch.DesktopSearch` exposes `/krunner` (`org.kde.krunner1`) and `/org/semsearch/SearchProvider` (`org.gnome.Shell.SearchProvider2`), returning ranked matches and launching deep-link actions, and that `execute_hit_action()`, `reveal_in_file_manager()`, clipboard helpers, and `open_terminal_at()` dispatch the expected Linux desktop handlers (including `jetbrains`, XDG default PDF viewer with `--`, `FileManager1` via `QDBusInterface`, and Qt clipboard).

#### Scenario: KRunner and GNOME SearchProvider2 end-to-end query and activation
- **WHEN** KRunner calls `Actions`, `Match`, and `Run`, or GNOME Shell calls `GetInitialResultSet`, `GetResultMetas`, `ActivateResult`, and `LaunchSearch`
- **THEN** both providers return consistent ranked metadata from `SearchService`, open files at exact page/line/timestamp anchors, and pre-fill the Quick Panel on `LaunchSearch`
