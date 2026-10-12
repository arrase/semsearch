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
    "gemma2",
    "vision",
    "audio",
    "llava",
    "bakllava",
    "embeddinggemma",
}


class OllamaClient:
    """Client for Ollama inference and multimodal model discovery."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout: float = 30.0,
        embed_timeout: float = 120.0,
        max_batch_size: int = 32,
        default_model: str = "embeddinggemma-2",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.embed_timeout = embed_timeout
        self.max_batch_size = max_batch_size
        self.default_model = default_model

    def get_tags(self) -> list[dict[str, Any]]:
        """Query available models from GET /api/tags."""
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=self.timeout)
        except httpx.ConnectError as err:
            raise OllamaConnectionError(f"Cannot connect to Ollama at {self.base_url}") from err
        except httpx.TimeoutException as err:
            raise OllamaTimeoutError(f"Request to Ollama timed out at {self.base_url}") from err
        except httpx.HTTPError as err:
            raise OllamaError(f"HTTP error: {err}") from err

        if response.status_code >= 400:
            raise OllamaError(f"Ollama error ({response.status_code}): {response.text}")

        data = response.json()
        return data.get("models", [])

    def show_model(self, model: str) -> dict[str, Any]:
        """Query model details and capabilities from POST /api/show."""
        try:
            response = httpx.post(
                f"{self.base_url}/api/show",
                json={"name": model, "model": model},
                timeout=self.timeout,
            )
        except httpx.ConnectError as err:
            raise OllamaConnectionError(f"Cannot connect to Ollama at {self.base_url}") from err
        except httpx.TimeoutException as err:
            raise OllamaTimeoutError(f"Request to Ollama timed out at {self.base_url}") from err
        except httpx.HTTPError as err:
            raise OllamaError(f"HTTP error: {err}") from err

        if response.status_code == 404:
            raise OllamaModelError(f"Model not found: {model}")
        if response.status_code >= 400:
            raise OllamaError(f"Ollama error ({response.status_code}): {response.text}")

        return response.json()

    def _is_info_multimodal(self, info: dict[str, Any], model_name: str = "") -> bool:
        """Check capabilities, details, architecture, and name for multimodal support."""
        caps = [str(c).lower() for c in info.get("capabilities", [])]
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
        if any(indicator in name.lower() for indicator in ("embeddinggemma", "vision", "audio", "llava", "bakllava")):
            return True

        return False

    def is_multimodal(self, model: str | dict[str, Any], tag_info: dict[str, Any] | None = None) -> bool:
        """Inspect capabilities, details, or architecture to check if a model is multimodal."""
        if isinstance(model, dict):
            return self._is_info_multimodal(model)

        model_name = model
        if tag_info and self._is_info_multimodal(tag_info, model_name=model_name):
            return True

        try:
            full_info = self.show_model(model_name)
            return self._is_info_multimodal(full_info, model_name=model_name)
        except OllamaError:
            return False

    def validate_model(self, model: str) -> bool:
        """Validate that model exists and supports multimodal embeddings."""
        if not self.is_multimodal(model):
            raise OllamaModelError(f"Model '{model}' does not support multimodal embeddings")
        return True

    def list_multimodal_models(self) -> list[str]:
        """Return list of model names that support multimodal embeddings."""
        models = self.get_tags()
        result: list[str] = []
        for item in models:
            name = item.get("name") or item.get("model", "")
            if not name:
                continue
            if self.is_multimodal(name, tag_info=item):
                result.append(name)
        return result

    def embed(
        self,
        model: str,
        inputs: list[str | dict[str, str]],
        truncate: bool = True,
    ) -> list[list[float]]:
        """Embed a heterogeneous batch of text, image, or audio inputs in chunks up to max_batch_size."""
        if not inputs:
            return []

        all_embeddings: list[list[float]] = []

        for i in range(0, len(inputs), self.max_batch_size):
            batch = inputs[i : i + self.max_batch_size]
            payload = {
                "model": model,
                "input": batch,
                "truncate": truncate,
            }
            try:
                response = httpx.post(
                    f"{self.base_url}/api/embed",
                    json=payload,
                    timeout=self.embed_timeout,
                )
            except httpx.ConnectError as err:
                raise OllamaConnectionError(f"Cannot connect to Ollama at {self.base_url}") from err
            except httpx.TimeoutException as err:
                raise OllamaTimeoutError(f"Embed request timed out at {self.base_url}") from err
            except httpx.HTTPError as err:
                raise OllamaError(f"HTTP error: {err}") from err

            if response.status_code == 404:
                raise OllamaModelError(f"Model not found: {model}")
            if response.status_code >= 400:
                raise OllamaError(f"Ollama error ({response.status_code}): {response.text}")

            data = response.json()
            embeddings = data.get("embeddings", [])
            all_embeddings.extend(embeddings)

        return all_embeddings
