# Design

## Context

See `proposal.md` for motivation. Currently in `SemSearch`:
- `SearchPanel.show_panel()` prepares and focuses the UI without notifying Ollama. The first request to Ollama happens only after the user types and pauses for the 120ms semantic debounce timer.
- `OllamaClient.embed()` resolves the model name via `resolve_model_name()` (which calls `GET /api/tags` every time without caching) and resolves `effective_ctx` (calling `get_model_context_length()` when `num_ctx == 0`).
- When the user types a multi-word query while Ollama is still loading weights into VRAM (2–4 seconds), every 120ms pause spawns a new `SearchWorker` (`QThread`). Calling `worker.stop()` on previous workers sets `_stopped = True` so their UI signals are ignored on completion, but because workers are not serialized before calling `search_service.search()`, every intermediate worker still sends a blocking `POST /api/embed` to Ollama, queuing obsolete requests ahead of the final query.

## Goals / Non-Goals

**Goals:**
- Start loading the embedding model into VRAM immediately when `SearchPanel.show_panel()` is invoked, overlapping cold-start weight loading with the user's typing time.
- Ensure `OllamaClient.preload()` and `OllamaClient.embed()` send identical `model` and `options` (`num_ctx`) so Ollama shares the same runner without reloading weights.
- Cache resolved model names in `OllamaClient.resolve_model_name()` per `(base_url, preferred)` to eliminate redundant `GET /api/tags` calls on every search.
- Prevent intermediate debounced `SearchWorker` threads from dispatching obsolete `/api/embed` requests while an earlier search is still running.

**Non-Goals:**
- Force-unloading the model from VRAM on `hide_panel()` or adding custom `keep_alive` configuration settings (Ollama's default expiration handles unloading).
- Changing the 120ms debounce interval or instant lexical FTS5 search behavior.

## Decisions

### 1. Preload via `POST /api/embed` with `"input": []` and shared `_resolve_target_and_ctx`
- **Choice**: Implement `OllamaClient.preload(model: str, num_ctx: int | None = None) -> None` using `/api/embed` with `{"model": target_model, "input": []}` (plus `"options": {"num_ctx": effective_ctx}` when `effective_ctx > 0`), sharing target model and context-window resolution with `embed()`.
- **Rationale**: Unlike generative chat models in `eloquent-notes` that use `/api/chat` with `"messages": []`, embedding models in Ollama use `/api/embed`. Sending `"input": []` loads the runner into VRAM and returns `{"embeddings": []}` without computing token embeddings. Matching `options.num_ctx` guarantees Ollama does not tear down and reload the runner on the first real `embed()` call.
- **Alternatives considered**:
  - Preloading with a dummy text string (`["warmup"]`): Wastes GPU compute on an unnecessary forward pass before the user's real query arrives.
  - Preloading without `options.num_ctx`: Causes Ollama to reload the model a second time when `embed()` passes `{"num_ctx": max_ctx}`.

### 2. Model Resolution Cache in `OllamaClient`
- **Choice**: Add `self._resolved_model_cache: dict[tuple[str, str], str] = {}` in `OllamaClient.__init__`. In `resolve_model_name(preferred)`, check `(self.base_url, preferred)` first; on a successful `get_tags()` call, store the resolved name under `(self.base_url, preferred)` before returning. If `get_tags()` raises `OllamaError`, return `preferred` without caching.
- **Rationale**: Eliminates an extra HTTP round-trip (`GET /api/tags`) on every single search query while still allowing recovery if Ollama was temporarily unreachable on an earlier attempt.

### 3. Single Daemon Preload Thread on `SearchPanel.show_panel()`
- **Choice**: In `SearchPanel`, track `self._preload_thread: threading.Thread | None = None`. When `show_panel()` is called, if `self.search_service.ollama is not None` and `(self._preload_thread is None or not self._preload_thread.is_alive())`, start a daemon `threading.Thread` targeting `self._preload_model()`, which calls `self.search_service.ollama.preload(self.search_service.model)` and ignores `OllamaError`.
- **Rationale**: Matches the proven pattern in `eloquent-notes`, keeps the Qt main thread completely non-blocking, covers all panel entry points (global hotkey, system tray action, `--panel` CLI/IPC), and avoids duplicate preload threads on rapid hotkey toggles.

### 4. Serializing `SearchWorker` Execution to Drop Obsolete Queries
- **Choice**: Guard `self.search_service.search(...)` inside `SearchWorker.run()` with a lock (`SearchWorker._search_lock` or per-panel/service lock) and re-check `if self.is_stopped: return` immediately after acquiring the lock.
- **Rationale**: When Worker 1 is waiting on Ollama (either cold-loading or embedding) and the user continues typing, `SearchPanel._stop_workers()` marks Worker 1 (and any subsequent intermediate Worker 2, Worker 3) as `_stopped = True`. Because Worker 2 and Worker 3 wait on the lock before calling `search_service.search()`, as soon as Worker 1 finishes, Worker 2 and Worker 3 acquire the lock, see `self.is_stopped == True`, and return in 0ms without calling Ollama or SQLite. Only the latest active worker (`is_stopped == False`) executes `search_service.search()`.

## Risks / Trade-offs

- **[Risk] Ollama is offline or unreachable when the user opens the search panel** → `SearchPanel._preload_model()` catches `OllamaError` silently in the daemon thread so lexical FTS5 search continues working without UI errors or delays.
- **[Risk] User changes `ollama_url` or `model` in Settings** → Both `_resolved_model_cache` and `_context_length_cache` are keyed by `(self.base_url, model)`, so changing either setting naturally misses the old key and resolves the new target cleanly.
