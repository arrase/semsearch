"""Unit tests for OllamaClient and multimodal discovery."""

import pytest
import httpx
from semsearch.core.ollama import (
    OllamaClient,
    OllamaError,
    OllamaConnectionError,
    OllamaTimeoutError,
    OllamaModelError,
)


def test_ollama_client_init() -> None:
    client = OllamaClient()
    assert client.base_url == "http://localhost:11434"
    assert client.timeout == 30.0
    assert client.embed_timeout == 120.0
    assert client.max_batch_size == 32
    assert client.default_model == "embeddinggemma-2"


def test_get_tags_success(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OllamaClient()

    def mock_get(url: str, timeout: float) -> httpx.Response:
        assert url == "http://localhost:11434/api/tags"
        assert timeout == 30.0
        return httpx.Response(
            200,
            json={"models": [{"name": "embeddinggemma-2:latest"}, {"name": "llama3:latest"}]},
        )

    monkeypatch.setattr(httpx, "get", mock_get)
    tags = client.get_tags()
    assert len(tags) == 2
    assert tags[0]["name"] == "embeddinggemma-2:latest"


def test_get_tags_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OllamaClient()

    def mock_connect_error(url: str, timeout: float) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx, "get", mock_connect_error)
    with pytest.raises(OllamaConnectionError):
        client.get_tags()

    def mock_timeout_error(url: str, timeout: float) -> httpx.Response:
        raise httpx.TimeoutException("Timed out")

    monkeypatch.setattr(httpx, "get", mock_timeout_error)
    with pytest.raises(OllamaTimeoutError):
        client.get_tags()

    def mock_server_error(url: str, timeout: float) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    monkeypatch.setattr(httpx, "get", mock_server_error)
    with pytest.raises(OllamaError):
        client.get_tags()


def test_show_model_success_and_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OllamaClient()

    def mock_post(url: str, json: dict, timeout: float) -> httpx.Response:
        if json.get("name") == "nonexistent":
            return httpx.Response(404, text="model not found")
        return httpx.Response(200, json={"capabilities": ["vision"], "details": {"family": "clip"}})

    monkeypatch.setattr(httpx, "post", mock_post)

    info = client.show_model("clip-vit")
    assert "vision" in info["capabilities"]

    with pytest.raises(OllamaModelError):
        client.show_model("nonexistent")


def test_is_multimodal() -> None:
    client = OllamaClient()

    # Capabilities array
    assert client.is_multimodal({"capabilities": ["vision", "text"]}) is True
    assert client.is_multimodal({"capabilities": ["audio"]}) is True
    assert client.is_multimodal({"capabilities": ["text"]}) is False
    assert client.is_multimodal({"capabilities": ["completion", "vision"]}) is False
    assert client.is_multimodal({"capabilities": ["embedding", "vision"]}) is True
    assert client.is_multimodal({"capabilities": ["completion", "embedding", "vision"]}) is True
    assert client.is_multimodal({"capabilities": ["completion", "vision", "tools", "thinking"]}) is False
    assert client.is_multimodal({"capabilities": ["COMPLETION", "VISION"]}) is False
    assert client.is_multimodal({"capabilities": ["EMBEDDING", "VISION"]}) is True
    assert client.is_multimodal({"capabilities": None}) is False

    # Details families
    assert client.is_multimodal({"details": {"families": ["clip", "text"]}}) is True
    assert client.is_multimodal({"details": {"family": "mllama"}}) is True
    assert client.is_multimodal({"details": {"family": "gemma-embedding2"}}) is True
    assert client.is_multimodal({"details": {"family": "gemma2"}}) is False
    assert client.is_multimodal({"details": {"family": "llama"}}) is False

    # Model info architecture
    assert client.is_multimodal({"model_info": {"general.architecture": "gemma-embedding2"}}) is True
    assert client.is_multimodal({"model_info": {"general.architecture": "gemma2"}}) is False
    assert client.is_multimodal({"model_info": {"clip.vision.embedding_length": 768}}) is True
    assert client.is_multimodal({"model_info": {"llama.block_count": 32}}) is False

    # Model name
    assert client.is_multimodal({"name": "embeddinggemma-2"}) is True
    assert client.is_multimodal({"name": "gemma2"}) is False
    assert client.is_multimodal({"name": "gemma2:latest"}) is False


def test_list_multimodal_models(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OllamaClient()

    def mock_get(url: str, timeout: float) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "embeddinggemma-2:latest", "details": {"family": "gemma-embedding2"}},
                    {"name": "llama3:latest", "details": {"family": "llama"}},
                    {"name": "llava:latest", "details": {"family": "llava"}},
                    {"name": "qwen3.8:latest", "capabilities": ["completion", "vision"]},
                ]
            },
        )

    def mock_post(url: str, json: dict, timeout: float) -> httpx.Response:
        name = json.get("name")
        if name == "llama3:latest":
            return httpx.Response(200, json={"capabilities": ["text"]})
        return httpx.Response(200, json={"capabilities": ["vision"]})

    monkeypatch.setattr(httpx, "get", mock_get)
    monkeypatch.setattr(httpx, "post", mock_post)

    multimodal = client.list_multimodal_models()
    assert "embeddinggemma-2:latest" in multimodal
    assert "llava:latest" in multimodal
    assert "llama3:latest" not in multimodal
    assert "qwen3.8:latest" not in multimodal


def test_embed_batching_and_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OllamaClient(max_batch_size=2)

    recorded_batches: list[list] = []

    def mock_post(url: str, json: dict, timeout: float) -> httpx.Response:
        assert timeout == 120.0
        batch = json["input"]
        recorded_batches.append(batch)
        # return dummy vector for each item
        embeddings = [[0.1, 0.2] for _ in batch]
        return httpx.Response(200, json={"embeddings": embeddings})

    monkeypatch.setattr(httpx, "post", mock_post)

    inputs = [
        "pre-prompted text query",
        {"image": "base64_data_1"},
        {"audio": "base64_data_2"},
        "another text string",
        {"image": "base64_data_3"},
    ]

    results = client.embed("embeddinggemma-2", inputs)
    assert len(results) == 5
    assert len(recorded_batches) == 3
    assert len(recorded_batches[0]) == 2
    assert len(recorded_batches[1]) == 2
    assert len(recorded_batches[2]) == 1


def test_embed_empty_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OllamaClient()
    assert client.embed("test", []) == []

    def mock_error(url: str, json: dict, timeout: float) -> httpx.Response:
        return httpx.Response(404, text="model not found")

    monkeypatch.setattr(httpx, "post", mock_error)
    with pytest.raises(OllamaModelError):
        client.embed("missing_model", ["hello"])


def test_validate_model(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OllamaClient()

    def mock_post(url: str, json: dict, timeout: float) -> httpx.Response:
        name = json.get("name")
        if name == "vision-model":
            return httpx.Response(200, json={"capabilities": ["vision"]})
        if name == "text-model":
            return httpx.Response(200, json={"capabilities": ["text"]})
        return httpx.Response(404, text="model not found")

    monkeypatch.setattr(httpx, "post", mock_post)

    assert client.validate_model("vision-model") is True

    with pytest.raises(OllamaModelError):
        client.validate_model("text-model")

    with pytest.raises(OllamaModelError):
        client.validate_model("nonexistent-model")


def test_is_multimodal_fallback_to_show_model(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OllamaClient()
    called_models: list[str] = []

    def mock_post(url: str, json: dict, timeout: float) -> httpx.Response:
        name = json.get("name")
        called_models.append(name)
        if name == "custom-model":
            return httpx.Response(200, json={"capabilities": ["vision"]})
        if name == "custom-text":
            return httpx.Response(200, json={"capabilities": ["text"]})
        if name == "generative-show":
            return httpx.Response(200, json={"capabilities": ["completion", "vision"]})
        if name == "gemma2-model":
            return httpx.Response(
                200,
                json={"details": {"family": "gemma2"}, "model_info": {"general.architecture": "gemma2"}},
            )
        return httpx.Response(500, text="server error")

    monkeypatch.setattr(httpx, "post", mock_post)

    # 1. tag_info confirms multimodal: show_model should NOT be called
    tag_confirmed = {"name": "llava-model"}
    assert client.is_multimodal("llava-model", tag_info=tag_confirmed) is True
    assert "llava-model" not in called_models

    # 2. tag_info has no indicators: falls back to show_model
    tag_unconfirmed = {"name": "custom-model", "size": 12345}
    assert client.is_multimodal("custom-model", tag_info=tag_unconfirmed) is True
    assert "custom-model" in called_models

    # 3. tag_info unconfirmed and show_model returns text-only
    tag_text = {"name": "custom-text", "size": 12345}
    assert client.is_multimodal("custom-text", tag_info=tag_text) is False
    assert "custom-text" in called_models

    # 4. tag_info unconfirmed and show_model raises OllamaError
    assert client.is_multimodal("error-model", tag_info={"name": "error-model"}) is False

    # 5. tag_info has completion capability: show_model should NOT be called
    tag_generative = {"name": "qwen-chat", "capabilities": ["completion", "vision"]}
    assert client.is_multimodal("qwen-chat", tag_info=tag_generative) is False
    assert "qwen-chat" not in called_models

    # 6. show_model returns completion capability: evaluates to False
    assert client.is_multimodal("generative-show") is False
    assert "generative-show" in called_models

    # 7. show_model returns gemma2 family and architecture: evaluates to False
    assert client.is_multimodal("gemma2-model") is False
    assert "gemma2-model" in called_models
