# Spec Delta

## Purpose

Provides a client interface for Ollama model discovery, multimodal capability inspection, and batch embedding generation supporting text, image, and audio inputs.

## ADDED Requirements

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
