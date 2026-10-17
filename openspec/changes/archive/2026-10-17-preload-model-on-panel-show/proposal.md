# Proposal

## Why

When the user opens the quick search panel and types a query after the embedding model has been unloaded from VRAM, cold-loading the model weights in Ollama takes several seconds. This cold-start latency is further compounded by redundant `/api/tags` lookups on every `embed()` call and by multiple debounced `SearchWorker` threads queuing obsolete `/api/embed` requests in Ollama while the user is still typing.

## What Changes

- Add `OllamaClient.preload(model, num_ctx=None)` to warm up the embedding model in VRAM via `POST /api/embed` with `"input": []` and the exact same resolved `num_ctx` options used by `embed()`, relying on Ollama's default `keep_alive` expiration rather than force-unloading on panel dismissal.
- Cache resolved model names per `(base_url, preferred)` in `OllamaClient.resolve_model_name()` so `embed()` and `preload()` avoid issuing redundant `GET /api/tags` requests on every invocation.
- Trigger asynchronous best-effort model preloading in a single deduplicated background thread when `SearchPanel.show_panel()` is called, overlapping VRAM weight loading with the user's focus and typing window.
- Serialize `SearchWorker` execution and skip workers marked stopped before invoking `search_service.search()` so rapid typing while Ollama is loading or computing embeddings never queues obsolete `/api/embed` requests.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `ollama-client`: Add `preload()` method with matching `num_ctx` options and per-host model name resolution caching in `resolve_model_name()`.
- `search-panel`: Trigger background model preload on `show_panel()` and serialize `SearchWorker` execution to drop obsolete in-flight queries before they reach Ollama.

## Impact

- `src/semsearch/core/ollama.py`: `OllamaClient` (`resolve_model_name`, `preload`, `embed`).
- `src/semsearch/gui/panel.py`: `SearchPanel` (`show_panel`, background preload thread management).
- `src/semsearch/gui/worker.py`: `SearchWorker` (serialized execution and pre-search stop check).
- `tests/test_ollama.py` and `tests/test_gui_panel.py`: Unit and widget tests covering preload, resolution caching, and worker serialization.
