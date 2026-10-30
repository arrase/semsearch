"""Ollama client and multimodal model discovery for SemSearch."""

from typing import Any

import httpx


class OllamaError(Exception):
    """Base exception for Ollama errors."""


class OllamaConnectionError(OllamaError):
    """Ollama daemon is unreachable."""


class OllamaTimeoutError(OllamaError):
    """Ollama request timed out."""


class OllamaModelError(OllamaError):
    """Ollama model is invalid or not found."""


MULTIMODAL_INDICATORS = {
    "clip",
    "mllama",
    "gemma-embedding2",
    "vision",
    "audio",
    "llava",
    "bakllava",
    "embeddinggemma",
}

_HTTP_NOT_FOUND = 404
_HTTP_BAD_REQUEST = 400
_MIN_PARAMETER_PARTS = 3


def format_query_prompt(text: str) -> str:
    """Format general document query prompt."""
    return f"task: search result | query: {text}"


def format_code_query_prompt(text: str) -> str:
    """Format source code retrieval query prompt."""
    return f"task: code retrieval | query: {text}"


def format_doc_prompt(title: str, text: str) -> str:
    """Format document embedding prompt with title context."""
    clean_title = title.strip() or "none"
    return f"title: {clean_title} | text: {text}"


class OllamaClient:
    """Client for Ollama inference and multimodal model discovery."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout: float = 30.0,
        embed_timeout: float = 120.0,
        max_batch_size: int = 32,
        *,
        default_model: str = "embeddinggemma-2",
        num_ctx: int = 0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.embed_timeout = embed_timeout
        self.max_batch_size = max_batch_size
        self.default_model = default_model
        self.num_ctx = num_ctx
        self._context_length_cache: dict[tuple[str, str], int] = {}
        self._resolved_model_cache: dict[tuple[str, str], str] = {}

    def _request(
        self,
        endpoint: str,
        timeout: float,
        payload: dict[str, Any] | None = None,
        model_name: str = "",
    ) -> dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        try:
            if payload is None:
                response = httpx.get(url, timeout=timeout)
            else:
                response = httpx.post(url, json=payload, timeout=timeout)
        except httpx.ConnectError as err:
            raise OllamaConnectionError(f"Cannot connect to Ollama at {self.base_url}") from err
        except httpx.TimeoutException as err:
            raise OllamaTimeoutError(f"Request to Ollama timed out at {self.base_url}") from err
        except httpx.HTTPError as err:
            raise OllamaError(f"HTTP error: {err}") from err

        if response.status_code == _HTTP_NOT_FOUND and model_name:
            raise OllamaModelError(f"Model not found: {model_name}")
        if response.status_code >= _HTTP_BAD_REQUEST:
            raise OllamaError(f"Ollama error ({response.status_code}): {response.text}")

        data: dict[str, Any] = response.json()
        return data

    def get_tags(self) -> list[dict[str, Any]]:
        """Query available models from GET /api/tags."""
        data = self._request("/api/tags", timeout=self.timeout)
        models: list[dict[str, Any]] = data.get("models", [])
        return models

    def show_model(self, model: str) -> dict[str, Any]:
        """Query model details and capabilities from POST /api/show."""
        return self._request(
            "/api/show",
            timeout=self.timeout,
            payload={"name": model, "model": model},
            model_name=model,
        )

    @staticmethod
    def _extract_context_length(info: dict[str, Any]) -> int | None:
        model_info = info.get("model_info") or {}
        candidates = [
            val
            for key, val in model_info.items()
            if key.endswith(".context_length") and isinstance(val, int) and val > 0
        ]
        details = info.get("details") or {}
        ctx = details.get("context_length")
        if isinstance(ctx, int) and ctx > 0:
            candidates.append(ctx)
        if candidates:
            return max(candidates)

        modelfile = str(info.get("modelfile") or "")
        for line in modelfile.splitlines():
            parts = line.strip().split()
            if (
                len(parts) >= _MIN_PARAMETER_PARTS
                and parts[0].upper() == "PARAMETER"
                and parts[1].lower() == "num_ctx"
                and parts[2].isdigit()
            ):
                return int(parts[2])
        return None

    def get_model_context_length(self, model: str) -> int | None:
        """Query the maximum supported context window size (in tokens) for a model from Ollama."""
        direct_key = (self.base_url, model)
        if direct_key in self._context_length_cache:
            return self._context_length_cache[direct_key]

        target = self.resolve_model_name(model)
        cache_key = (self.base_url, target)
        if cache_key in self._context_length_cache:
            return self._context_length_cache[cache_key]

        try:
            info = self.show_model(target)
        except OllamaError:
            return None

        max_ctx = self._extract_context_length(info)
        if max_ctx is not None:
            self._context_length_cache[cache_key] = max_ctx
            self._context_length_cache[direct_key] = max_ctx
        return max_ctx

    def _is_info_multimodal(self, info: dict[str, Any], model_name: str = "") -> bool:
        """Check capabilities, details, architecture, and name for multimodal support."""
        caps = [str(c).lower() for c in (info.get("capabilities") or [])]
        if "completion" in caps and "embedding" not in caps:
            return False
        if any(c in ("vision", "audio") for c in caps):
            return True

        details = info.get("details") or {}
        families = [str(f).lower() for f in details.get("families", [])]
        if details.get("family"):
            families.append(str(details["family"]).lower())
        if any(f in MULTIMODAL_INDICATORS for f in families):
            return True

        model_info = info.get("model_info") or {}
        arch = str(model_info.get("general.architecture", "")).lower()
        if any(indicator in arch for indicator in MULTIMODAL_INDICATORS):
            return True

        if any(k.startswith(("clip.", "mllama.", "vision.", "audio.")) or "projector" in k for k in model_info):
            return True

        name = model_name or str(info.get("name") or info.get("model") or "")
        return any(
            indicator in name.lower() for indicator in ("embeddinggemma", "vision", "audio", "llava", "bakllava")
        )

    def is_multimodal(self, model: str | dict[str, Any], tag_info: dict[str, Any] | None = None) -> bool:
        """Inspect capabilities, details, or architecture to check if a model is multimodal."""
        if isinstance(model, dict):
            return self._is_info_multimodal(model)

        if tag_info:
            caps = [str(c).lower() for c in (tag_info.get("capabilities") or [])]
            if "completion" in caps and "embedding" not in caps:
                return False
            if self._is_info_multimodal(tag_info, model_name=model):
                return True

        try:
            full_info = self.show_model(model)
            return self._is_info_multimodal(full_info, model_name=model)
        except OllamaError:
            return False

    def resolve_model_name(self, preferred: str) -> str:
        """Resolve model name, falling back to installed multimodal model if preferred is not found."""
        cache_key = (self.base_url, preferred)
        if cache_key in self._resolved_model_cache:
            return self._resolved_model_cache[cache_key]

        try:
            tags = self.get_tags()
        except OllamaError:
            return preferred

        names = [str(item.get("name") or item.get("model", "")) for item in tags]
        if preferred in names:
            self._resolved_model_cache[cache_key] = preferred
            return preferred

        clean_pref = preferred.lower().replace("-", "").replace("_", "")
        for n in names:
            clean_n = n.lower().replace("-", "").replace("_", "")
            if clean_pref in clean_n or clean_n in clean_pref:
                self._resolved_model_cache[cache_key] = n
                self._resolved_model_cache[(self.base_url, n)] = n
                return n

        self._resolved_model_cache[cache_key] = preferred
        return preferred

    def validate_model(self, model: str) -> bool:
        """Validate that model exists and supports multimodal embeddings."""
        if not self.is_multimodal(model):
            resolved = self.resolve_model_name(model)
            if not self.is_multimodal(resolved):
                raise OllamaModelError(f"Model '{model}' does not support multimodal embeddings")
        return True

    def list_multimodal_models(self) -> list[str]:
        """Return list of model names that support multimodal embeddings."""
        result: list[str] = []
        for item in self.get_tags():
            name = item.get("name") or item.get("model", "")
            if name and self.is_multimodal(name, tag_info=item):
                result.append(name)
        return result

    def _resolve_target_and_ctx(self, model: str, num_ctx: int | None) -> tuple[str, int]:
        target_model = self.resolve_model_name(model)
        effective_ctx = self.num_ctx if num_ctx is None else num_ctx
        if effective_ctx == 0:
            max_ctx = self.get_model_context_length(target_model)
            if max_ctx is not None:
                effective_ctx = max_ctx
        return target_model, effective_ctx

    def preload(self, model: str, num_ctx: int | None = None) -> None:
        """Preload embedding model into VRAM without computing token embeddings."""
        target_model, effective_ctx = self._resolve_target_and_ctx(model, num_ctx)
        payload: dict[str, Any] = {
            "model": target_model,
            "input": [],
        }
        if effective_ctx > 0:
            payload["options"] = {"num_ctx": effective_ctx}
        self._request(
            "/api/embed",
            timeout=self.embed_timeout,
            payload=payload,
            model_name=model,
        )

    def embed(
        self,
        model: str,
        inputs: list[str | dict[str, str]],
        *,
        truncate: bool = True,
        num_ctx: int | None = None,
    ) -> list[list[float]]:
        """Embed a heterogeneous batch of text, image, or audio inputs in chunks up to max_batch_size."""
        if not inputs:
            return []

        target_model, effective_ctx = self._resolve_target_and_ctx(model, num_ctx)
        all_embeddings: list[list[float]] = []

        for i in range(0, len(inputs), self.max_batch_size):
            batch = inputs[i : i + self.max_batch_size]
            payload: dict[str, Any] = {
                "model": target_model,
                "input": batch,
                "truncate": truncate,
            }
            if effective_ctx > 0:
                payload["options"] = {"num_ctx": effective_ctx}
            data = self._request(
                "/api/embed",
                timeout=self.embed_timeout,
                payload=payload,
                model_name=model,
            )
            all_embeddings.extend(data.get("embeddings", []))

        return all_embeddings
