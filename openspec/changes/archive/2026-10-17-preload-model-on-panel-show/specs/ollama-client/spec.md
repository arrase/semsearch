# Spec Delta

## ADDED Requirements

### Requirement: Embedding Model VRAM Preloading
The Ollama client MUST provide a `preload(model, num_ctx=None)` method that sends a `POST /api/embed` request with an empty input array (`"input": []`) and the resolved `options` (`num_ctx`) matching `embed()`, loading the embedding model into VRAM without computing token embeddings or overriding Ollama's default `keep_alive` timer.

#### Scenario: Preloading model with resolved context window
- **WHEN** `preload(model)` is invoked on `OllamaClient`
- **THEN** a `POST /api/embed` request is sent with `"model"` set to the resolved model name, `"input": []`, and `"options": {"num_ctx": <effective_ctx>}` matching `embed()`, without a custom `keep_alive` override

#### Scenario: Preloading model with explicit positive num_ctx
- **WHEN** `preload(model, num_ctx=8192)` is invoked on `OllamaClient`
- **THEN** the `POST /api/embed` request includes `"input": []` and `"options": {"num_ctx": 8192}`

### Requirement: Model Name Resolution Caching
The Ollama client MUST cache resolved model names keyed by `(base_url, preferred)` when `resolve_model_name()` succeeds so subsequent `embed()`, `preload()`, and context-length lookups reuse the resolved name without issuing redundant `GET /api/tags` requests.

#### Scenario: Reusing cached model name across multiple embed calls
- **WHEN** `resolve_model_name(model)` or `embed(model, inputs)` is called multiple times for the same `base_url` and `model`
- **THEN** `GET /api/tags` is queried only on the first resolution and cached for subsequent calls

#### Scenario: Skipping cache when tags lookup fails
- **WHEN** `resolve_model_name(model)` encounters an `OllamaError` while querying `GET /api/tags`
- **THEN** it returns the preferred model name without caching the failed lookup so a later call can retry resolution
