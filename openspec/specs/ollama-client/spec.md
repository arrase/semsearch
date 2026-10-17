# ollama-client Specification

## Purpose
Provides a client interface for Ollama model discovery, multimodal capability inspection, and batch embedding generation supporting text, image, and audio inputs.

## Requirements

### Requirement: Multimodal Model Capability Discovery and Filtering
The Ollama client MUST inspect available models from `/api/tags` and `/api/show` to discover their input capabilities, and MUST filter the model selection list to include exclusively models supporting multimodal embeddings (vision or audio).

#### Scenario: Multimodal model discovery
- **WHEN** the client queries available models from Ollama
- **THEN** only models with vision or audio embedding capabilities are returned as valid selectable models

#### Scenario: Text-only model exclusion
- **WHEN** an Ollama model possesses text-only capabilities without multimodal projectors
- **THEN** the model is filtered out and excluded from selectable multimodal embedding models

### Requirement: Multimodal Batch Embedding Generation
The Ollama client MUST provide a batch embedding method for `/api/embed` accepting an array of heterogeneous inputs, including text strings, image base64 objects `{"image": "<base64>"}`, and audio base64 objects `{"audio": "<base64>"}`, returning a list of embedding vector floats.

#### Scenario: Heterogeneous batch embedding
- **WHEN** the client submits a batch containing text, image base64, and audio base64 items
- **THEN** Ollama `/api/embed` is invoked with the payload and returns the corresponding normalized embedding vectors for each item

#### Scenario: Batch chunking for large requests
- **WHEN** the client receives a list of inputs exceeding the maximum batch threshold
- **THEN** the inputs are partitioned into smaller sub-batches and sequentially dispatched without exceeding service limits

### Requirement: Default Model Selection and Validation
The system MUST configure `embeddinggemma-2` as the default embedding model, and MUST validate any user-selected model to ensure it is multimodal.

#### Scenario: Default model assignment
- **WHEN** no custom model is configured by the user
- **THEN** the default model identifier is set to `embeddinggemma-2`

#### Scenario: Rejection of invalid non-multimodal model
- **WHEN** a user configuration specifies a model lacking multimodal embedding support
- **THEN** the client flags an error and refuses to activate the incompatible model

### Requirement: Robust Error Handling and Timeouts
The Ollama client MUST enforce separate HTTP timeouts for metadata queries and embedding requests, and MUST translate HTTP or connection failures into domain exceptions.

#### Scenario: Connection failure handling
- **WHEN** Ollama is unreachable at the configured base URL
- **THEN** an `OllamaConnectionError` is raised indicating service unavailability

#### Scenario: Request timeout handling
- **WHEN** an embedding batch request exceeds the configured timeout threshold
- **THEN** an `OllamaTimeoutError` is raised and any active HTTP connection is closed cleanly

### Requirement: Maximum Context Window Discovery and Automatic Resolution
The Ollama client MUST query the model's maximum supported context window via `get_model_context_length(model)` by selecting the maximum positive integer across `/api/show` context length fields and caching the resolved value per model. When the effective `num_ctx` is `0`, `embed()` MUST resolve the target model's maximum context window and pass `{"num_ctx": <max_ctx>}` in `options` on `/api/embed` requests when a positive value is found.

#### Scenario: Discovering maximum context length across multiple model_info entries
- **WHEN** `get_model_context_length(model)` is called and `/api/show` returns multiple `.context_length` entries or `details.context_length`
- **THEN** the highest positive integer value among those entries is returned and cached for subsequent lookups

#### Scenario: Embedding with num_ctx set to 0 resolves model maximum context window
- **WHEN** `embed()` is invoked with `num_ctx` configured to `0` (either on the client or per-call) and `get_model_context_length()` returns a positive integer `max_ctx`
- **THEN** the `/api/embed` request payload includes `"options": {"num_ctx": max_ctx}`

#### Scenario: Embedding with explicit positive num_ctx uses configured value
- **WHEN** `embed()` is invoked with an explicit positive `num_ctx` value
- **THEN** the `/api/embed` request payload includes `"options": {"num_ctx": num_ctx}` without overriding it with the model's maximum

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

