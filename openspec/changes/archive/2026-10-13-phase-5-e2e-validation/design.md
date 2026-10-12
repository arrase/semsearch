# Design

## Context

Phases 1 through 4 established the complete SemSearch architecture:
- Core storage: SQLite schema (`files`, `segments`, `vectors`), FTS5 lexical index, and vector math routines.
- Multimodal extractors: specialized native extractors for images, PDFs, documents, media (audio/video), and source code/notebooks adhering strictly to a zero OCR policy.
- Ollama client: local inference integration supporting multimodal embeddings defaulting to `embeddinggemma-2`.
- Services: crawler engine, two-stage pipelined indexing worker, inotify filesystem watcher, power-aware battery scheduler, hybrid DigUp ranking search service, and Linux desktop action deep links.
- Desktop GUI: PySide6 (Qt 6) floating quick search panel, standalone full search window with kind chips and card grid, system tray progress ring, 4-tab settings dialog, 5-step onboarding wizard, single-instance socket lock, and CLI argument dispatcher.

Phase 5 represents the capstone integration, validation, polish, and regression hardening phase. It ties all components together into an end-to-end validated system verified against synthetic multimodal testbeds, search fidelity benchmarks, CLI activation flows, and the complete DigUp feature matrix.

## Goals / Non-Goals

**Goals:**
- Provide a self-contained, realistic multimodal testbed generator (`tests/testbed.py`) synthesizing sample files across all supported modalities (JPEG, PNG, digital/scanned PDFs, TXT, MD, DOCX, WAV, MP4, PY, IPYNB).
- Verify full indexing pipeline execution (crawl -> extract -> embed -> store) against primary `semsearch.db` and code `code.db` SQLite databases.
- Validate cross-modal search fidelity (text descriptions matching visual images/frames), BM25 keyword boosting, serial/symbol lookup query routing, and code search with definition line anchors.
- Validate hybrid DigUp ranking math, quality gating ($Z < 2.0 \land \text{coverage} < 0.5$), and folded weaker matches partitioning ($< 0.5 \times \text{topScore}$).
- Verify CLI execution flags (`--panel`, `--window`, `--settings`, `--daemon`) and IPC message forwarding over `QLocalServer`/`QLocalSocket`.
- Verify desktop deep links (`xdg-open`, `mpv`/`vlc`, `okular`/`evince`, code editors at line, file manager reveal).
- Enforce strict system invariants: zero OCR across all extractors, `embeddinggemma-2` default configuration, and full GUI component parity.

**Non-Goals:**
- Introducing new file modalities beyond those specified in Phases 1-4.
- Altering the stable SQLite schema or database table layouts.
- Adding remote or cloud-based embedding/LLM integrations (pure local Ollama only).
- Supporting non-Linux operating systems.

## Decisions

### 1. Synthetic Multimodal Testbed Generator (`tests/testbed.py`)

A standalone generator creates deterministic, valid sample files across all modalities inside a temporary directory for test fixtures. No network access or heavy external assets are required.

```
<testbed_root>/
├── images/
│   ├── photo_sunset.jpg        (Pillow RGB JPEG)
│   └── screenshot_code.png     (Pillow RGBA high-resolution PNG)
├── pdfs/
│   ├── digital_report.pdf      (PyMuPDF document with >200 characters text)
│   └── scanned_diagram.pdf     (PyMuPDF document with visual vector diagram and minimal text)
├── docs/
│   ├── readme.md               (Markdown headers, lists, and formatted text)
│   ├── notes.txt               (Plain text document)
│   └── brief.docx              (python-docx document with styled paragraphs)
├── media/
│   ├── speech_audio.wav        (Synthesized multi-tone PCM WAV audio with silence)
│   └── clip_video.mp4          (OpenCV / PyAV synthesized MP4 with video frames)
└── code/
    ├── service.py              (Python script with classes, methods, functions, and docstrings)
    └── analysis.ipynb          (Jupyter notebook JSON structure with stripped output cells)
```

**Public API:**
```python
def generate_testbed(target_dir: Path) -> dict[str, Path]:
    """Generate realistic synthetic test files across all supported modalities."""
```

### 2. End-to-End Pipeline Indexing Architecture (`tests/test_e2e_pipeline.py`)

The end-to-end pipeline test exercises the real indexing flow against real SQLite databases on disk:
1. `Crawler` traverses the generated testbed directory respecting include/exclude rules.
2. `IndexService` coordinates two-stage extraction:
   - Extractors produce `ExtractedSegment` instances with appropriate `Modality` and `SegmentKind`.
   - `OllamaClient` (or simulated embedding provider) batches vectors.
   - `Database.insert_file()` and `Database.insert_segment()` populate database records.
   - FTS5 lexical index is populated with tokenized texts.
3. Assertions verify:
   - All generated testbed files are indexed without errors.
   - Primary database `semsearch.db` contains segments for images, PDFs, docs, audio, and video.
   - Code database `code.db` contains segments for `service.py` and `analysis.ipynb` with symbol definitions.
4. Incremental update test:
   - Modify content of `notes.txt`.
   - Run incremental indexing pass.
   - Verify `mtime`, segment text, and vectors are updated while old segments are refreshed.

### 3. Search Fidelity & Quality Gating Architecture (`tests/test_e2e_search.py`)

Verifies hybrid search routing, ranking calculations, quality gating, and weaker match folding:
1. **Cross-Modal Semantic Search**:
   - Query: `"golden sunset over ocean landscape"`
   - Evaluates vector cosine similarity and normalized Z-score against image segments.
   - Asserts `photo_sunset.jpg` is retrieved as top result.
2. **Lookup Serial Search**:
   - Query: `"ERR-4096-FAIL"` or `"0xdeadbeef"`
   - `is_lookup_query` detects serial/token pattern.
   - Applies lookup weighting: $w_{\text{meaning}} = 0.5, w_{\text{words}} = 4.0$.
   - Asserts exact keyword match is ranked first.
3. **Code Search with Line Anchors**:
   - Query: `"code: def calculate_metrics"`
   - Routes to `code_db`.
   - Locates definition line matching `calculate_metrics` in `service.py`.
   - Asserts hit returned with `focus` equal to the definition line number and $0.3$ hint bonus applied.
4. **Quality Gating Enforcement**:
   - Verifies that any candidate segment where $Z < 2.0$ and $\text{coverage} < 0.5$ is strictly filtered out.
5. **Weaker Match Folding**:
   - Verifies that hits with $\text{score} < 0.5 \times \text{topScore}$ are partitioned into `ResultSet.weaker`, while hits with $\text{score} \ge 0.5 \times \text{topScore}$ populate `ResultSet.ranked`.

### 4. CLI Dispatch & Single-Instance IPC Architecture (`tests/test_e2e_cli.py`)

Verifies command-line argument handling and IPC activation:
1. `build_parser()` parses:
   - `--panel` -> Action `SHOW_PANEL`
   - `--window` -> Action `SHOW_WINDOW`
   - `--settings` -> Action `SHOW_SETTINGS`
   - `--daemon` -> Action `DAEMON`
2. Single-instance lock test:
   - Primary `SemSearchApp` instance binds `QLocalServer` on `semsearch-app.sock`.
   - Secondary process calls `forward_ipc_command(action)`.
   - Verifies `QLocalSocket` connects, writes UTF-8 action string, receives acknowledgment, and exits cleanly.
   - Verifies primary instance receives action and invokes corresponding UI method (`show_panel()`, `show_window()`, `show_settings()`).

### 5. Desktop Action Deep Links Architecture (`tests/test_e2e_actions.py`)

Verifies exact process arguments dispatched by `execute_hit_action()`:
- `FileKind.CODE` with `focus=42` -> launches `[editor, "-g", "file:42"]` or `[ide, "--line", "42", "file"]`.
- `FileKind.PDF` with `loc=3` -> launches `["okular", "-p", "3", "file"]` or `["evince", "-p", "3", "file"]`.
- `FileKind.VIDEO` or `AUDIO` with `loc=45.0` -> launches `["mpv", "--start=45", "file"]` or `["vlc", "--start-time=45", "file"]`.
- Fallback -> launches `["xdg-open", "file"]`.
- File manager reveal -> sends DBus message `org.freedesktop.FileManager1.ShowItems` or launches `[filemanager, "--select", "file"]`.

### 6. Architectural Invariants Verification (`tests/test_e2e_invariants.py`)

Guarantees non-negotiable architectural constraints:
1. **Zero OCR Invariant**:
   - Scans AST and source code of all extractor modules (`images.py`, `pdfs.py`, `docs.py`, `media.py`, `code.py`).
   - Asserts total absence of OCR libraries: `tesseract`, `pytesseract`, `easyocr`, `paddleocr`.
2. **Multimodal Model Configuration Invariant**:
   - Asserts `AppConfig().model == "embeddinggemma-2"`.
   - Inspects `SettingsDialog` and `OnboardingWizard` model lists to verify non-multimodal models (e.g. text-only models) are excluded.
3. **GUI Parity Invariant**:
   - Validates existence and correct instantiation of all 5 core UI widgets:
     - `SearchPanel`: borderless floating panel with two-column layout and weaker matches folding.
     - `SearchWindow`: standalone window with kind filter chips (`Ctrl+1`..`Ctrl+7`) and card grid.
     - `SystemTrayManager`: circular progress ring dynamically drawn via `QPainter`.
     - `SettingsDialog`: 4 tabs (Folders, Code, Storage & Model, General).
     - `OnboardingWizard`: 5 sequential setup steps.

## Risks / Trade-offs

- [Synthetic Video Generation Failure] → Generating MP4 files with OpenCV requires appropriate codecs (`avc1` or `mp4v`). If video encoding libraries are unavailable in certain test environments, fallback to minimal valid container generation or stubbed media headers ensuring zero test fragility.
- [Headless Display Environment] → PySide6 widgets require an active display server or offscreen platform. All GUI integration tests run under `QT_QPA_PLATFORM=offscreen` to ensure reliable execution in CI and headless containers.
- [Live Ollama Availability] → Networked or local Ollama instances may not be running during routine automated unit runs. Core search tests use deterministic embedding stubs, while dedicated live Ollama integration tests use conditional skipping (`pytest.mark.skipif(not ollama_available)`).
