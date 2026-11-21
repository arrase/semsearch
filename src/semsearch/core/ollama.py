"""Ollama HTTP client and multimodal embedding model discovery for SemSearch."""

import threading
from types import TracebackType
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
    "embeddinggemma",
}

_HTTP_NOT_FOUND = 404
_HTTP_BAD_REQUEST = 400
_MIN_PARAMETER_PARTS = 3
_MULTIMODAL_BATCH_SIZE = 4
_EMBED_ENDPOINT = "/api/embed"


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
    """Persistent keep-alive client for Ollama inference and multimodal embedding discovery."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout: float = 30.0,
        embed_timeout: float = 120.0,
        max_batch_size: int = 32,
        *,
        num_ctx: int = 0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.embed_timeout = embed_timeout
        self.max_batch_size = max_batch_size
        self.num_ctx = num_ctx
        self._cache_lock = threading.Lock()
        self._context_length_cache: dict[tuple[str, str], int] = {}
        self._resolved_model_cache: dict[tuple[str, str], str] = {}
        self._client = httpx.Client()

    @property
    def base_url(self) -> str:
        """Normalized Ollama base URL without trailing slash."""
        return self._base_url

    @base_url.setter
    def base_url(self, value: str) -> None:
        normalized = value.rstrip("/")
        with self._cache_lock:
            if normalized != self._base_url:
                self._base_url = normalized
                self._context_length_cache.clear()
                self._resolved_model_cache.clear()

    def close(self) -> None:
        """Close the persistent HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> "OllamaClient":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def _request(
        self,
        endpoint: str,
        timeout: float,
        payload: dict[str, Any] | None = None,
        model_name: str = "",
    ) -> dict[str, Any]:
        base_url = self.base_url
        url = f"{base_url}{endpoint}"
        try:
            if payload is None:
                response = self._client.get(url, timeout=timeout)
            else:
                response = self._client.post(url, json=payload, timeout=timeout)
        except httpx.ConnectError as err:
            raise OllamaConnectionError(f"Cannot connect to Ollama at {base_url}") from err
        except httpx.TimeoutException as err:
            raise OllamaTimeoutError(f"Request to Ollama timed out at {base_url}") from err
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
        base_url = self.base_url
        direct_key = (base_url, model)
        with self._cache_lock:
            if direct_key in self._context_length_cache:
                return self._context_length_cache[direct_key]

        target = self.resolve_model_name(model)
        cache_key = (base_url, target)
        with self._cache_lock:
            if cache_key in self._context_length_cache:
                return self._context_length_cache[cache_key]

        try:
            info = self.show_model(target)
        except OllamaError:
            return None

        max_ctx = self._extract_context_length(info)
        if max_ctx is not None:
            with self._cache_lock:
                self._context_length_cache[cache_key] = max_ctx
                self._context_length_cache[direct_key] = max_ctx
        return max_ctx

    def _is_info_multimodal(self, info: dict[str, Any], model_name: str = "") -> bool:
        """Check verified capabilities, details, architecture, and name for multimodal embedding support."""
        caps = [str(c).lower() for c in (info.get("capabilities") or [])]
        if "embedding" not in caps:
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
        return any(indicator in name.lower() for indicator in ("embeddinggemma", "vision", "audio", "clip"))

    def is_multimodal(self, model: str | dict[str, Any], tag_info: dict[str, Any] | None = None) -> bool:
        """Inspect verified /api/show capabilities and metadata to check multimodal embedding support."""
        if isinstance(model, dict):
            return self._is_info_multimodal(model)

        if tag_info and "capabilities" in tag_info:
            tag_caps = [str(c).lower() for c in (tag_info.get("capabilities") or [])]
            if "embedding" not in tag_caps:
                return False

        try:
            full_info = self.show_model(model)
        except (OllamaConnectionError, OllamaTimeoutError):
            raise
        except OllamaError:
            return False
        return self._is_info_multimodal(full_info, model_name=model)

    def resolve_model_name(self, preferred: str) -> str:
        """Resolve model name, falling back to an installed matching tag if preferred is not exact."""
        base_url = self.base_url
        cache_key = (base_url, preferred)
        with self._cache_lock:
            if cache_key in self._resolved_model_cache:
                return self._resolved_model_cache[cache_key]

        try:
            tags = self.get_tags()
        except OllamaError:
            return preferred

        names = [str(item.get("name") or item.get("model", "")) for item in tags]
        if preferred in names:
            with self._cache_lock:
                self._resolved_model_cache[cache_key] = preferred
            return preferred

        clean_pref = preferred.lower().replace("-", "").replace("_", "")
        for name in names:
            clean_name = name.lower().replace("-", "").replace("_", "")
            if clean_pref in clean_name or clean_name in clean_pref:
                with self._cache_lock:
                    self._resolved_model_cache[cache_key] = name
                    self._resolved_model_cache[(base_url, name)] = name
                return name

        with self._cache_lock:
            self._resolved_model_cache[cache_key] = preferred
        return preferred

    def validate_model(self, model: str) -> bool:
        """Validate that model exists and supports multimodal embeddings, propagating connectivity errors."""
        if self.is_multimodal(model):
            return True
        resolved = self.resolve_model_name(model)
        if resolved != model and self.is_multimodal(resolved):
            return True
        raise OllamaModelError(f"Model '{model}' does not support multimodal embeddings")

    def list_multimodal_models(self) -> list[str]:
        """Return installed model names verified via /api/show to support multimodal embeddings."""
        result: list[str] = []
        for item in self.get_tags():
            name = str(item.get("name") or item.get("model", ""))
            if name and self.is_multimodal(name, tag_info=item):
                result.append(name)
        return result

    def _resolve_target_and_ctx(self, model: str, num_ctx: int | None) -> tuple[str, int]:
        target_model = self.resolve_model_name(model)
        effective_ctx = self.num_ctx if num_ctx is None else num_ctx
        return target_model, max(0, effective_ctx)

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
            _EMBED_ENDPOINT,
            timeout=self.embed_timeout,
            payload=payload,
            model_name=model,
        )

    def unload(self, model: str) -> None:
        """Unload embedding model from VRAM immediately."""
        target_model = self.resolve_model_name(model)
        payload: dict[str, Any] = {
            "model": target_model,
            "input": [],
            "keep_alive": 0,
        }
        self._request(
            _EMBED_ENDPOINT,
            timeout=self.timeout,
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
        """Embed a heterogeneous batch of text, image, or audio inputs with adaptive batch sizing."""
        if not inputs:
            return []

        target_model, effective_ctx = self._resolve_target_and_ctx(model, num_ctx)
        multimodal_batch = min(self.max_batch_size, _MULTIMODAL_BATCH_SIZE)
        all_embeddings: list[list[float]] = []

        idx = 0
        while idx < len(inputs):
            window = inputs[idx : idx + self.max_batch_size]
            batch = inputs[idx : idx + multimodal_batch] if any(isinstance(item, dict) for item in window) else window
            idx += len(batch)

            payload: dict[str, Any] = {
                "model": target_model,
                "input": batch,
                "truncate": truncate,
            }
            if effective_ctx > 0:
                payload["options"] = {"num_ctx": effective_ctx}
            data = self._request(
                _EMBED_ENDPOINT,
                timeout=self.embed_timeout,
                payload=payload,
                model_name=model,
            )
            batch_embs = data.get("embeddings")
            if not isinstance(batch_embs, list) or len(batch_embs) != len(batch):
                got = len(batch_embs) if isinstance(batch_embs, list) else 0
                raise OllamaError(f"Ollama returned {got} embeddings for batch of {len(batch)}")
            all_embeddings.extend(batch_embs)

        return all_embeddings
