# ollama-client Specification

## Purpose
Provides a persistent HTTP client interface for Ollama model discovery, strict multimodal embedding capability inspection, adaptive batch embedding generation across text/image/audio inputs, context-window management without VRAM inflation, and explicit VRAM preload/unload lifecycle control.

## Requirements

### Requirement: Persistent Connection Pool and Thread-Safe Base URL Switching
`OllamaClient` MUST maintain a persistent keep-alive `httpx.Client` instance with `close()` and context-manager (`__enter__`/`__exit__`) teardown. Its `base_url` property setter MUST normalize URLs via `rstrip("/")` and atomically clear `_context_length_cache` and `_resolved_model_cache` under a `threading.Lock` whenever the endpoint URL changes.

#### Scenario: Changing Ollama base URL invalidates caches cleanly
- **WHEN** `client.base_url` is updated to a different host URL (with or without a trailing slash)
- **THEN** the stored URL is normalized without a trailing slash and both `_context_length_cache` and `_resolved_model_cache` are cleared atomically under `_cache_lock`

#### Scenario: Releasing HTTP connection pool on close
- **WHEN** `client.close()` is called or the client exits a `with OllamaClient(...)` block
- **THEN** the underlying persistent `httpx.Client` pool is closed cleanly

### Requirement: Strict Multimodal Embedding Capability Discovery and Filtering
`OllamaClient` MUST inspect models from `GET /api/tags` and `POST /api/show` and MUST require the `"embedding"` capability to be explicitly present before considering any model valid for multimodal embeddings, excluding both text-only embedding models and generative vision-language chat models (such as `llava`). `is_multimodal()` and `validate_model()` MUST propagate `OllamaConnectionError` and `OllamaTimeoutError` instead of masking connectivity failures as invalid models.

#### Scenario: Multimodal embedding model discovery
- **WHEN** `list_multimodal_models()` queries available models from Ollama
- **THEN** only installed models verified via `/api/show` to possess `"embedding"` alongside vision/audio capability, multimodal projector keys, or multimodal embedding indicators are returned

#### Scenario: Generative vision chat model and text-only model exclusion
- **WHEN** an installed model supports `"vision"` + `"completion"` without `"embedding"`, or supports `"embedding"` for text only
- **THEN** `is_multimodal()` returns `False` and `validate_model()` raises `OllamaModelError`

#### Scenario: Propagating connectivity failures during validation
- **WHEN** `validate_model()` or `is_multimodal()` encounters an unreachable daemon or HTTP timeout
- **THEN** `OllamaConnectionError` or `OllamaTimeoutError` is raised directly rather than returning `False` or raising `OllamaModelError`

### Requirement: Adaptive Multimodal Batch Embedding and Response Validation
`OllamaClient.embed()` MUST dispatch heterogeneous inputs (text strings, `{"image": "<base64>"}`, `{"audio": "<base64>"}`) to `POST /api/embed` using adaptive batching: up to `max_batch_size` (`32` by default) for text-only windows and `min(max_batch_size, 4)` (`4`) whenever the active window contains any Base64 multimodal dictionary payload. It MUST validate that `len(embeddings) == len(batch)` and raise `OllamaError` on count mismatch.

#### Scenario: Adaptive batch sizing for text versus multimodal payloads
- **WHEN** `embed()` receives 40 text inputs versus 10 Base64 image/audio dictionary inputs
- **THEN** text inputs are dispatched in batches of up to 32 while windows containing Base64 dictionary payloads are dispatched in sub-batches of at most 4

#### Scenario: Validating returned embedding count per batch
- **WHEN** Ollama returns fewer or more embeddings than the submitted batch size
- **THEN** `embed()` raises `OllamaError` instead of silently truncating or misaligning vectors

### Requirement: Context Window Resolution Without VRAM Inflation (`num_ctx == 0`)
`OllamaClient.get_model_context_length(model)` MUST query `/api/show` to discover and cache the maximum supported context length (from `*.context_length`, `details.context_length`, or `PARAMETER num_ctx` in `modelfile`) for UI validation. When effective `num_ctx == 0`, `embed()` and `preload()` MUST omit `"options"` (and `"options.num_ctx"`) from the `/api/embed` request payload so Ollama uses the model's real default context window without inflating KV-cache VRAM. `"options": {"num_ctx": effective_ctx}` MUST be included only when `effective_ctx > 0`.

#### Scenario: Embedding with num_ctx set to 0 omits options.num_ctx
- **WHEN** `embed()` or `preload()` is invoked with effective `num_ctx == 0`
- **THEN** the `POST /api/embed` request payload omits `"options"` so Ollama applies the model's native default context window without inflating VRAM

#### Scenario: Embedding with explicit positive num_ctx passes configured value
- **WHEN** `embed()` or `preload()` is invoked with an explicit positive `num_ctx` (e.g., `8192`)
- **THEN** the `POST /api/embed` request payload includes `"options": {"num_ctx": 8192}`

### Requirement: VRAM Preloading, Idle Unloading, and Thread-Safe Name Caching
`OllamaClient` MUST provide `preload(model, num_ctx=None)` sending `POST /api/embed` with `"input": []` (without overriding `keep_alive`) and `unload(model)` sending `POST /api/embed` with `"input": []` and `"keep_alive": 0` to free VRAM immediately. `resolve_model_name()` MUST cache successful resolutions keyed by `(base_url, preferred)` under `_cache_lock` and skip caching on `OllamaError`.

#### Scenario: Unloading model from VRAM when idle
- **WHEN** `unload(model)` is invoked on `OllamaClient`
- **THEN** a `POST /api/embed` request is sent with `"model"` set to the resolved model name, `"input": []`, and `"keep_alive": 0`

#### Scenario: Reusing cached model name across requests
- **WHEN** `resolve_model_name(model)` succeeds and subsequent `embed()` or `preload()` calls run against the same `base_url`
- **THEN** `GET /api/tags` is not queried repeatedly until `base_url` changes; failed tag lookups are not cached
