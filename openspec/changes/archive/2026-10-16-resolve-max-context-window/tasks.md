# Tasks

## 1. Ollama Context Window Resolution

- [x] 1.1 Update `OllamaClient.get_model_context_length()` in `src/semsearch/core/ollama.py` to select the maximum positive integer across all `*.context_length` entries in `model_info` and `details.context_length` (falling back to `PARAMETER num_ctx` in `modelfile`), cache resolved values per `(self.base_url, target)` in `self._context_length_cache`, and verify with `.venv/bin/pytest tests/test_ollama.py -k test_get_model_context_length`
- [x] 1.2 Update `OllamaClient.embed()` in `src/semsearch/core/ollama.py` to resolve `effective_ctx == 0` via `self.get_model_context_length(target_model)` and pass `options: {"num_ctx": effective_ctx}` when a positive value is found, updating unit tests in `tests/test_ollama.py` and verifying with `.venv/bin/pytest tests/test_ollama.py`
