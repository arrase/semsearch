# Spec Delta

## ADDED Requirements

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
