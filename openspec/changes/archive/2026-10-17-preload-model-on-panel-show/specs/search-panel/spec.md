# Spec Delta

## ADDED Requirements

### Requirement: Asynchronous Model Preloading on Panel Invocation
When `show_panel()` surfaces the quick search panel, the panel MUST launch a background daemon thread to preload the configured embedding model into VRAM via `OllamaClient.preload()`, ensuring at most one preload thread is active at a time and ignoring connection or model errors so the UI remains responsive. Dismissing the panel via `hide_panel()` MUST NOT force-unload the model from VRAM.

#### Scenario: Preloading embedding model when search panel opens
- **WHEN** `show_panel()` is called to display the quick search panel
- **THEN** a background daemon thread invokes `preload()` for the configured model without blocking the UI thread

#### Scenario: Deduplicating preload threads on rapid panel toggles
- **WHEN** `show_panel()` is invoked while a previous preload thread is still alive
- **THEN** no second concurrent preload thread is spawned

#### Scenario: Preserving model in VRAM when panel is dismissed
- **WHEN** `hide_panel()` dismisses the quick search panel
- **THEN** no unload request is sent to Ollama, leaving the model loaded until Ollama's default `keep_alive` expires

### Requirement: Obsolete Semantic Search Request Skipping
Background `SearchWorker` threads MUST serialize execution of `search_service.search()` and verify `is_stopped` immediately after acquiring the execution lock so intermediate workers cancelled while an earlier request is still waiting on Ollama exit immediately without dispatching obsolete `/api/embed` requests.

#### Scenario: Skipping intermediate debounced queries queued during slow model load
- **WHEN** multiple debounced queries trigger new `SearchWorker` instances while the first worker is still executing `search_service.search()`
- **THEN** intermediate workers marked stopped while waiting on the execution lock exit immediately upon acquiring the lock without calling `search_service.search()`, and only the latest active query executes
