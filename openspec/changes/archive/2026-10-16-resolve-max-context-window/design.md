# Design

## Context

See `proposal.md` for motivation. Currently in `src/semsearch/core/ollama.py`:
- `OllamaClient.get_model_context_length(model)` inspects `/api/show` and returns the first key in `model_info` ending with `.context_length`.
- `OllamaClient.embed()` checks `if effective_ctx > 0:` before setting `payload["options"] = {"num_ctx": effective_ctx}`. When `num_ctx == 0`, `options` is omitted, causing Ollama to load the model with its internal minimum default context window (2048/4096) rather than the model's maximum context length (e.g., 262144 for `embeddinggemma-2`).

## Goals / Non-Goals

**Goals:**
- Ensure `get_model_context_length(model)` returns the maximum context window reported by Ollama across `model_info` (`*.context_length`) and `details.context_length`, with fallback to `modelfile` `PARAMETER num_ctx`.
- Cache resolved context lengths per `(base_url, target_model)` in `OllamaClient` so `embed()` does not repeat `/api/show` HTTP calls on every batch.
- Automatically resolve `effective_ctx == 0` in `embed()` to `get_model_context_length(target_model)` and pass `options: {"num_ctx": effective_ctx}` when a positive value is obtained.

**Non-Goals:**
- Changing the configuration schema or replacing `0` in `config.json` / the Settings UI text field with a hardcoded integer. `0` remains the dynamic "use model maximum" sentinel.

## Decisions

- **Resolve `0` dynamically in `OllamaClient.embed()` with in-memory caching**:
  - *Rationale*: Resolving `0` inside `OllamaClient` ensures both initial onboarding indexing and subsequent model switches automatically use the active model's maximum context window without mutating the user's saved `0` preference. Caching `dict[tuple[str, str], int]` keyed by `(self.base_url, target_model)` avoids an extra `/api/show` request on every `embed()` call.
  - *Alternatives considered*: Resolving `0` only when saving in `SettingsDialog`. Rejected because first-run onboarding and default configs would still run with `0` un-resolved unless the user manually opened and saved Settings.
- **Select `max()` across all context length candidates in `/api/show`**:
  - *Rationale*: Multimodal GGUF metadata in `model_info` can contain multiple keys ending in `.context_length` (e.g., text backbone vs. sub-encoders) as well as `details.context_length`. Collecting all positive `int` values from `model_info` `*.context_length` and `details.context_length` and taking `max(candidates)` guarantees the model's full context window is selected rather than an arbitrary first dictionary entry.

## Risks / Trade-offs

- **[Risk] `/api/show` fails or does not report any context length for a custom model** → `get_model_context_length()` returns `None`, `effective_ctx` remains `0`, and `embed()` omits `options.num_ctx` so embedding still succeeds using Ollama's fallback.
