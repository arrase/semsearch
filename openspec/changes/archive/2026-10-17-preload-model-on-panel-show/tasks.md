# Tasks

## 1. Ollama Client Preload and Resolution Caching

- [x] 1.1 Add `_resolved_model_cache` to `OllamaClient` in `src/semsearch/core/ollama.py` so `resolve_model_name()` caches successful `(base_url, preferred)` lookups without caching failed `get_tags()` calls, and verify with unit tests in `tests/test_ollama.py`
- [x] 1.2 Implement `OllamaClient.preload(model, num_ctx=None)` in `src/semsearch/core/ollama.py` using `POST /api/embed` with `"input": []` and shared `num_ctx` option resolution matching `embed()`, and verify with unit tests in `tests/test_ollama.py` (`uv run pytest tests/test_ollama.py`)

## 2. Search Panel Preload and Obsolete Search Skipping

- [x] 2.1 Serialize `SearchWorker.run()` execution in `src/semsearch/gui/worker.py` with an execution lock and re-check `if self.is_stopped: return` immediately after acquiring the lock so stopped intermediate workers exit without calling `search_service.search()`, and verify with tests in `tests/test_gui_panel.py`
- [x] 2.2 Trigger asynchronous best-effort model preloading in a single deduplicated background daemon thread (`_preload_thread`) when `SearchPanel.show_panel()` is called in `src/semsearch/gui/panel.py` without force-unloading on `hide_panel()`, and verify with tests in `tests/test_gui_panel.py` (`uv run pytest tests/test_gui_panel.py`)
