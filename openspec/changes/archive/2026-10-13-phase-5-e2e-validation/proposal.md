# Proposal

## Why

SemSearch has established its core storage, Ollama embeddings, multimodal extractors, hybrid ranking search services, and PySide6 desktop GUI across Phases 1 through 4. To ensure release readiness and strict parity with the DigUp design paradigm, Phase 5 delivers comprehensive end-to-end integration testing, synthetic realistic testbed validation across all supported modalities, live Ollama search evaluation, CLI and single-instance IPC activation verification, strict architectural invariant auditing, and final validation against the DigUp feature matrix.

## What Changes

- Implement synthetic multimodal testbed generator `tests/testbed.py` producing realistic test files across all supported media:
  - Images & Screenshots (JPEG, PNG).
  - PDFs with digital text pages and rendered slide/scanned visual pages without OCR.
  - Documents (plain text `.txt`, Markdown `.md`, Word `.docx`).
  - Audio (WAV files with synthetic voice tones and silence intervals).
  - Video (MP4 files containing synthesized visual frames and audio track).
  - Code & Notebooks (Python `.py` with classes and functions, Jupyter `.ipynb` with stripped outputs).
- Implement end-to-end indexing pipeline tests in `tests/test_e2e_pipeline.py` verifying file crawling, two-stage extraction, database persistence, and incremental inotify change processing across `semsearch.db` and `code.db`.
- Implement search fidelity and quality gating tests in `tests/test_e2e_search.py` verifying cross-modal semantic search, BM25 keyword boosting, serial/symbol lookup query routing, `code:` prefix search with line anchors, and weaker match folding.
- Implement CLI invocation and IPC dispatch tests in `tests/test_e2e_cli.py` verifying `--panel`, `--window`, `--settings`, and `--daemon` arguments alongside `QLocalServer`/`QLocalSocket` single-instance message forwarding.
- Implement desktop action deep-link tests in `tests/test_e2e_actions.py` verifying exact CLI invocations for system handlers (`xdg-open`), media playback (`mpv`, `vlc`), PDF viewers (`okular`, `evince`, `zathura`), code editors (`code -g`, `cursor -g`, `zed`, `subl`, JetBrains IDEs), and file manager file reveals.
- Implement architectural invariants audit in `tests/test_e2e_invariants.py` verifying zero OCR dependencies or invocations, `embeddinggemma-2` default configuration, multimodal model dropdown filtering, and full GUI component parity.

## Capabilities

### New Capabilities
- `e2e-validation`: End-to-end integration testbed, multimodal search fidelity verification, CLI invocation validation, and architectural invariant enforcement.

### Modified Capabilities
None.

## Impact

- Adds testbed generation utilities in `tests/testbed.py` creating clean, self-contained fixtures across all supported file kinds without external network dependencies.
- Introduces end-to-end integration and invariant test suites: `tests/test_e2e_pipeline.py`, `tests/test_e2e_search.py`, `tests/test_e2e_cli.py`, `tests/test_e2e_actions.py`, and `tests/test_e2e_invariants.py`.
- Exercises all system modules: `config.py`, `database.py`, `ollama.py`, `vectors.py`, extractors (`images.py`, `pdfs.py`, `docs.py`, `media.py`, `code.py`), `crawler.py`, `indexer.py`, `searcher.py`, `watcher.py`, `scheduler.py`, `actions.py`, and `gui/` (`theme.py`, `panel.py`, `preview.py`, `window.py`, `tray.py`, `settings.py`, `onboarding.py`, `hotkey.py`, `app.py`).
- Requires zero new external runtime dependencies.
- Fully compatible with offscreen headless test execution (`QT_QPA_PLATFORM=offscreen`).
