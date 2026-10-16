# Proposal

## Why

When `num_ctx` is configured to `0` (the default setting), `OllamaClient.embed()` currently omits `options.num_ctx` from the `/api/embed` request payload. Because Ollama defaults to a small minimum context window (typically 2048 or 4096 tokens) when `options.num_ctx` is omitted, models like `embeddinggemma-2` are loaded with their minimum default context window instead of their maximum supported context window (e.g., 262144 tokens) reported by the Ollama API. Additionally, `get_model_context_length()` returns the first `.context_length` entry in `model_info` rather than the maximum across all context length fields.

## What Changes

- Update `OllamaClient.get_model_context_length(model)` to compute and return the maximum positive integer value across all `*.context_length` keys in `model_info` and `details.context_length` (falling back to `PARAMETER num_ctx` in `modelfile` if none are present), and cache the resolved maximum context length per model.
- Update `OllamaClient.embed()` so that when the effective `num_ctx` is `0`, it resolves the model's maximum context window via `get_model_context_length(target_model)` and passes `{"num_ctx": max_ctx}` in `payload["options"]` whenever a positive maximum context length is available.

## Capabilities

### New Capabilities
<!-- None -->

### Modified Capabilities
- `ollama-client`: Specify maximum context window discovery (`get_model_context_length`) and automatic resolution of `num_ctx = 0` to the model's maximum context window in `embed()` requests.

## Impact

- `src/semsearch/core/ollama.py`: `OllamaClient.get_model_context_length` and `OllamaClient.embed`, plus per-model context length caching.
- `tests/test_ollama.py`: Unit tests covering `max()` selection across multiple `.context_length` keys and `embed()` passing `options: {"num_ctx": <max_ctx>}` when `num_ctx == 0`.
