# Tasks

## 1. Multimodal Synthetic Testbed Generator

- [x] 1.1 Implement synthetic image and screenshot generation (JPEG and PNG) in `tests/testbed.py`, verifying file creation and image dimensions.
- [x] 1.2 Implement synthetic PDF generation (digital text page with >200 characters and scanned diagram page without OCR) in `tests/testbed.py`, verifying PyMuPDF page rendering.
- [x] 1.3 Implement synthetic document generation (plain text `.txt`, Markdown `.md`, and Word `.docx`) in `tests/testbed.py`, verifying text content and formatting.
- [x] 1.4 Implement synthetic audio and video generation (PCM WAV with voice frequencies and MP4 with visual frames) in `tests/testbed.py`, verifying container metadata and playback integrity.
- [x] 1.5 Implement synthetic code and notebook generation (Python script with AST symbols and Jupyter `.ipynb` with stripped outputs) in `tests/testbed.py`, verifying symbol parsing.
- [x] 1.6 Implement unit tests in `tests/test_testbed.py` verifying end-to-end generation of the complete testbed directory structure.

## 2. End-to-End Indexing Pipeline Verification

- [x] 2.1 Implement end-to-end full crawler and indexing test in `tests/test_e2e_pipeline.py` verifying that all testbed files are indexed into `semsearch.db` and `code.db` without errors.
- [x] 2.2 Implement database consistency assertions in `tests/test_e2e_pipeline.py` verifying counts and foreign key relationships across `files`, `segments`, and `vectors` tables.
- [x] 2.3 Implement incremental indexing test in `tests/test_e2e_pipeline.py` modifying and deleting testbed files and verifying database updates via `IndexService`.
- [x] 2.4 Implement inotify file system watcher integration test in `tests/test_e2e_pipeline.py` verifying real-time file event detection and queued indexing.

## 3. Cross-Modal & Semantic Search Fidelity Verification

- [x] 3.1 Implement cross-modal text-to-image semantic search test in `tests/test_e2e_search.py` verifying that visual queries retrieve relevant testbed image hits.
- [x] 3.2 Implement cross-modal text-to-video semantic search test in `tests/test_e2e_search.py` verifying that scene descriptions retrieve target video timestamp segments.
- [x] 3.3 Implement search evaluation harness against local live Ollama instance with `embeddinggemma-2` in `tests/test_e2e_search.py`, verifying live embedding generation and retrieval accuracy.

## 4. Lexical BM25 & Lookup Serial Search Verification

- [x] 4.1 Implement full-text FTS5 BM25 search test in `tests/test_e2e_search.py` verifying exact keyword matching and term ranking.
- [x] 4.2 Implement identifier and serial lookup query routing test in `tests/test_e2e_search.py` verifying that hyphenated serials, camelCase symbols, and hex tokens activate lookup weighting.
- [x] 4.3 Implement term coverage calculation test in `tests/test_e2e_search.py` verifying multi-term and prefix matching behavior.

## 5. Code Search & Definition Line Anchor Verification

- [x] 5.1 Implement code search routing test in `tests/test_e2e_search.py` verifying that `code:` prefix queries isolate search to `code.db`.
- [x] 5.2 Implement symbol definition matching test in `tests/test_e2e_search.py` verifying that function and class queries anchor to the exact definition line number in `focus`.
- [x] 5.3 Implement Jupyter notebook code search test in `tests/test_e2e_search.py` verifying that stripped code cells are retrieved with appropriate line locations.

## 6. Quality Gating & Weaker Matches Verification

- [x] 6.1 Implement quality gating test in `tests/test_e2e_search.py` verifying that candidate hits with normalized Z-score < 2.0 and coverage < 0.5 are pruned from results.
- [x] 6.2 Implement folded weaker matches test in `tests/test_e2e_search.py` verifying that candidate hits scoring below 50% of the top hit score are partitioned into `ResultSet.weaker`.
- [x] 6.3 Implement result view model formatting test in `tests/test_e2e_search.py` verifying `ResultRow` conversion and snippet highlighting.

## 7. CLI Activation & Single-Instance IPC Verification

- [x] 7.1 Implement CLI flag parsing test in `tests/test_e2e_cli.py` verifying parser handling of `--panel`, `--window`, `--settings`, and `--daemon`.
- [x] 7.2 Implement single-instance socket lock test in `tests/test_e2e_cli.py` verifying `QLocalServer` creation and collision detection.
- [x] 7.3 Implement IPC command forwarding test in `tests/test_e2e_cli.py` verifying that `SHOW_PANEL`, `SHOW_WINDOW`, `SHOW_SETTINGS`, and `DAEMON` messages trigger appropriate app actions and exit secondary processes.

## 8. Desktop Action Deep Linking Verification

- [x] 8.1 Implement desktop action test in `tests/test_e2e_actions.py` verifying code editor CLI invocation with line targets (`code -g`, `cursor -g`, `zed`, `subl`, PyCharm `--line`).
- [x] 8.2 Implement document viewer action test in `tests/test_e2e_actions.py` verifying PDF page navigation CLI arguments (`okular -p`, `evince -p`, `zathura -P`).
- [x] 8.3 Implement media player action test in `tests/test_e2e_actions.py` verifying playback timestamp CLI arguments (`mpv --start`, `vlc --start-time`).
- [x] 8.4 Implement file manager reveal test in `tests/test_e2e_actions.py` verifying DBus invocation and fallback file manager selection flags.

## 9. Architectural Invariants & Parity Audit

- [x] 9.1 Implement static AST and code scan in `tests/test_e2e_invariants.py` verifying zero OCR dependencies or invocations across all extractor modules.
- [x] 9.2 Implement configuration invariant test in `tests/test_e2e_invariants.py` verifying default model `embeddinggemma-2` and strict multimodal model filtering in GUI settings.
- [x] 9.3 Implement GUI component parity test in `tests/test_e2e_invariants.py` verifying instantiations and offscreen rendering of `SearchPanel`, `SearchWindow`, `SystemTrayManager`, `SettingsDialog`, and `OnboardingWizard`.

## 10. Full Validation & Strict OpenSpec Verification

- [x] 10.1 Execute complete test suite with `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -v` ensuring zero regressions across all modules.
- [x] 10.2 Validate Phase 5 change specifications strictly with `openspec validate phase-5-e2e-validation --strict`.
