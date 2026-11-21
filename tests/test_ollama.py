"""Unit tests for OllamaClient, persistent HTTP pooling, multimodal discovery, and batching."""

import httpx
import pytest

from semsearch.core.ollama import (
    OllamaClient,
    OllamaConnectionError,
    OllamaError,
    OllamaModelError,
    OllamaTimeoutError,
)


def test_ollama_client_init_and_context_manager() -> None:
    """Verify initialization defaults, URL normalization, cache invalidation, and close."""
    with OllamaClient(base_url="http://localhost:11434///") as client:
        assert client.base_url == "http://localhost:11434"
        assert client.timeout == 30.0
        assert client.embed_timeout == 120.0
        assert client.max_batch_size == 32
        assert client.num_ctx == 0

        client._resolved_model_cache[(client.base_url, "m")] = "m:latest"
        client._context_length_cache[(client.base_url, "m")] = 8192

        client.base_url = "http://127.0.0.1:11434/"
        assert client.base_url == "http://127.0.0.1:11434"
        assert client._resolved_model_cache == {}
        assert client._context_length_cache == {}

    assert client._client.is_closed is True


def test_get_tags_success(monkeypatch: pytest.MonkeyPatch) -> None:
    with OllamaClient() as client:

        def mock_get(url: str, timeout: float) -> httpx.Response:
            assert url == "http://localhost:11434/api/tags"
            assert timeout == 30.0
            return httpx.Response(
                200,
                json={"models": [{"name": "embeddinggemma-2:latest"}, {"name": "llama3:latest"}]},
            )

        monkeypatch.setattr(client._client, "get", mock_get)
        tags = client.get_tags()
        assert len(tags) == 2
        assert tags[0]["name"] == "embeddinggemma-2:latest"


def test_get_tags_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    with OllamaClient() as client:

        def mock_connect_error(url: str, timeout: float) -> httpx.Response:
            del url, timeout
            raise httpx.ConnectError("Connection refused")

        monkeypatch.setattr(client._client, "get", mock_connect_error)
        with pytest.raises(OllamaConnectionError):
            client.get_tags()

        def mock_timeout_error(url: str, timeout: float) -> httpx.Response:
            del url, timeout
            raise httpx.TimeoutException("Timed out")

        monkeypatch.setattr(client._client, "get", mock_timeout_error)
        with pytest.raises(OllamaTimeoutError):
            client.get_tags()

        def mock_server_error(url: str, timeout: float) -> httpx.Response:
            del url, timeout
            return httpx.Response(500, text="Internal Server Error")

        monkeypatch.setattr(client._client, "get", mock_server_error)
        with pytest.raises(OllamaError):
            client.get_tags()


def test_show_model_success_and_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    with OllamaClient() as client:

        def mock_post(url: str, json: dict[str, object], timeout: float) -> httpx.Response:
            del url, timeout
            if json.get("name") == "nonexistent":
                return httpx.Response(404, text="model not found")
            return httpx.Response(200, json={"capabilities": ["embedding", "vision"], "details": {"family": "clip"}})

        monkeypatch.setattr(client._client, "post", mock_post)

        info = client.show_model("clip-vit")
        assert "vision" in info["capabilities"]

        with pytest.raises(OllamaModelError):
            client.show_model("nonexistent")


def test_is_multimodal_requires_embedding_capability() -> None:
    """Verify multimodal detection always requires the 'embedding' capability."""
    with OllamaClient() as client:
        # Without 'embedding' capability, even vision/multimodal metadata is rejected
        assert client.is_multimodal({"capabilities": ["vision", "text"]}) is False
        assert client.is_multimodal({"capabilities": ["audio"]}) is False
        assert client.is_multimodal({"capabilities": ["text"]}) is False
        assert client.is_multimodal({"capabilities": ["completion", "vision"]}) is False
        assert client.is_multimodal({"capabilities": ["completion", "vision", "tools", "thinking"]}) is False
        assert client.is_multimodal({"capabilities": ["COMPLETION", "VISION"]}) is False
        assert client.is_multimodal({"capabilities": None}) is False
        assert client.is_multimodal({"details": {"families": ["clip", "text"]}}) is False
        assert client.is_multimodal({"name": "embeddinggemma-2"}) is False

        # With 'embedding' capability + multimodal indicators -> True
        assert client.is_multimodal({"capabilities": ["embedding", "vision"]}) is True
        assert client.is_multimodal({"capabilities": ["embedding", "audio"]}) is True
        assert client.is_multimodal({"capabilities": ["completion", "embedding", "vision"]}) is True
        assert client.is_multimodal({"capabilities": ["EMBEDDING", "VISION"]}) is True
        assert client.is_multimodal({"capabilities": ["embedding"], "details": {"families": ["clip", "text"]}}) is True
        assert client.is_multimodal({"capabilities": ["embedding"], "details": {"family": "gemma-embedding2"}}) is True
        assert (
            client.is_multimodal(
                {"capabilities": ["embedding"], "model_info": {"general.architecture": "gemma-embedding2"}}
            )
            is True
        )
        assert (
            client.is_multimodal({"capabilities": ["embedding"], "model_info": {"clip.vision.embedding_length": 768}})
            is True
        )
        assert client.is_multimodal({"capabilities": ["embedding"], "name": "embeddinggemma-2"}) is True

        # Text-only embedding models -> False
        assert client.is_multimodal({"capabilities": ["embedding"], "details": {"family": "gemma2"}}) is False
        assert (
            client.is_multimodal({"capabilities": ["embedding"], "model_info": {"general.architecture": "nomic-bert"}})
            is False
        )
        assert client.is_multimodal({"capabilities": ["embedding"], "name": "nomic-embed-text:latest"}) is False


def test_list_multimodal_models_verifies_show_and_rejects_chat_vlms(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify /api/tags alone never admits chat VLMs like llava without /api/show embedding capability."""
    with OllamaClient() as client:
        show_calls: list[str] = []

        def mock_get(url: str, timeout: float) -> httpx.Response:
            del url, timeout
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "embeddinggemma-2:latest", "details": {"family": "gemma-embedding2"}},
                        {"name": "llama3:latest", "details": {"family": "llama"}},
                        {"name": "llava:latest", "details": {"family": "llava", "families": ["llava", "clip"]}},
                        {"name": "qwen3.8:latest", "capabilities": ["completion", "vision"]},
                        {"name": "nomic-embed-text:latest", "details": {"family": "nomic-bert"}},
                    ]
                },
            )

        def mock_post(url: str, json: dict[str, object], timeout: float) -> httpx.Response:
            del url, timeout
            name = str(json.get("name", ""))
            show_calls.append(name)
            if name == "embeddinggemma-2:latest":
                return httpx.Response(
                    200,
                    json={"capabilities": ["embedding", "vision"], "details": {"family": "gemma-embedding2"}},
                )
            if name == "llava:latest":
                return httpx.Response(
                    200,
                    json={"capabilities": ["completion", "vision"], "details": {"family": "llava"}},
                )
            if name == "nomic-embed-text:latest":
                return httpx.Response(200, json={"capabilities": ["embedding"], "details": {"family": "nomic-bert"}})
            return httpx.Response(200, json={"capabilities": ["completion"]})

        monkeypatch.setattr(client._client, "get", mock_get)
        monkeypatch.setattr(client._client, "post", mock_post)

        multimodal = client.list_multimodal_models()
        assert multimodal == ["embeddinggemma-2:latest"]
        assert "llava:latest" in show_calls
        assert "qwen3.8:latest" not in show_calls  # Short-circuited via explicit tag capabilities


def test_embed_adaptive_batching_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify pure text uses max_batch_size (32), binary dicts cap batch size to 4, and mismatched lengths raise."""
    with OllamaClient(max_batch_size=8) as client:
        recorded_batches: list[list[object]] = []

        def mock_get(url: str, timeout: float) -> httpx.Response:
            del url, timeout
            return httpx.Response(200, json={"models": [{"name": "embeddinggemma-2"}]})

        def mock_post(url: str, json: dict[str, object], timeout: float) -> httpx.Response:
            assert url == "http://localhost:11434/api/embed"
            assert timeout == 120.0
            batch = json["input"]
            assert isinstance(batch, list)
            recorded_batches.append(batch)
            embeddings = [[0.1, 0.2] for _ in batch]
            return httpx.Response(200, json={"embeddings": embeddings})

        monkeypatch.setattr(client._client, "get", mock_get)
        monkeypatch.setattr(client._client, "post", mock_post)

        # Pure text: 10 items -> batches [8, 2]
        text_res = client.embed("embeddinggemma-2", [f"t{i}" for i in range(10)])
        assert len(text_res) == 10
        assert [len(b) for b in recorded_batches] == [8, 2]

        # Binary dicts: 6 items -> capped to multimodal batch size 4 -> batches [4, 2]
        recorded_batches.clear()
        media_inputs: list[str | dict[str, str]] = [{"image": f"img_{i}"} for i in range(6)]
        media_res = client.embed("embeddinggemma-2", media_inputs)
        assert len(media_res) == 6
        assert [len(b) for b in recorded_batches] == [4, 2]

        # Mismatched embeddings length raises OllamaError
        def mock_short_post(url: str, json: dict[str, object], timeout: float) -> httpx.Response:
            del url, json, timeout
            return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})

        monkeypatch.setattr(client._client, "post", mock_short_post)
        with pytest.raises(OllamaError, match="returned 1 embeddings for batch of 2"):
            client.embed("embeddinggemma-2", ["a", "b"])


def test_embed_empty_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with OllamaClient() as client:
        assert client.embed("test", []) == []

        def mock_get(url: str, timeout: float) -> httpx.Response:
            del url, timeout
            return httpx.Response(200, json={"models": []})

        def mock_error(url: str, json: dict[str, object], timeout: float) -> httpx.Response:
            del url, json, timeout
            return httpx.Response(404, text="model not found")

        monkeypatch.setattr(client._client, "get", mock_get)
        monkeypatch.setattr(client._client, "post", mock_error)
        with pytest.raises(OllamaModelError):
            client.embed("missing_model", ["hello"])


def test_validate_model_and_error_propagation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify validate_model accepts multimodal embedding models and propagates connection/timeout errors."""
    with OllamaClient() as client:

        def mock_get(url: str, timeout: float) -> httpx.Response:
            del url, timeout
            return httpx.Response(200, json={"models": []})

        def mock_post(url: str, json: dict[str, object], timeout: float) -> httpx.Response:
            del url, timeout
            name = json.get("name")
            if name == "vision-embed":
                return httpx.Response(200, json={"capabilities": ["embedding", "vision"]})
            if name == "text-embed":
                return httpx.Response(200, json={"capabilities": ["embedding"]})
            return httpx.Response(404, text="model not found")

        monkeypatch.setattr(client._client, "get", mock_get)
        monkeypatch.setattr(client._client, "post", mock_post)

        assert client.validate_model("vision-embed") is True

        with pytest.raises(OllamaModelError):
            client.validate_model("text-embed")

        with pytest.raises(OllamaModelError):
            client.validate_model("nonexistent-model")

        def mock_connect_fail(url: str, json: dict[str, object], timeout: float) -> httpx.Response:
            del url, json, timeout
            raise httpx.ConnectError("down")

        monkeypatch.setattr(client._client, "post", mock_connect_fail)
        with pytest.raises(OllamaConnectionError):
            client.validate_model("vision-embed")

        def mock_timeout_fail(url: str, json: dict[str, object], timeout: float) -> httpx.Response:
            del url, json, timeout
            raise httpx.TimeoutException("slow")

        monkeypatch.setattr(client._client, "post", mock_timeout_fail)
        with pytest.raises(OllamaTimeoutError):
            client.validate_model("vision-embed")


def test_get_model_context_length(monkeypatch: pytest.MonkeyPatch) -> None:
    with OllamaClient() as client:
        show_calls: list[str] = []

        def mock_get(url: str, timeout: float) -> httpx.Response:
            del url, timeout
            return httpx.Response(200, json={"models": [{"name": "embeddinggemma-2:latest"}]})

        def mock_post(url: str, json: dict[str, object], timeout: float) -> httpx.Response:
            del url, timeout
            name = str(json.get("name", ""))
            show_calls.append(name)
            if name == "embeddinggemma-2:latest":
                return httpx.Response(
                    200,
                    json={
                        "model_info": {
                            "clip.context_length": 4096,
                            "gemma-embedding2.context_length": 262144,
                        },
                        "details": {"context_length": 8192},
                    },
                )
            if name == "details-model":
                return httpx.Response(200, json={"details": {"context_length": 8192}})
            if name == "modelfile-model":
                return httpx.Response(200, json={"modelfile": "FROM base\nPARAMETER num_ctx 16384\n"})
            return httpx.Response(404, text="not found")

        monkeypatch.setattr(client._client, "get", mock_get)
        monkeypatch.setattr(client._client, "post", mock_post)

        assert client.get_model_context_length("embeddinggemma-2") == 262144
        assert client.get_model_context_length("embeddinggemma-2") == 262144
        assert show_calls.count("embeddinggemma-2:latest") == 1
        assert client.get_model_context_length("details-model") == 8192
        assert client.get_model_context_length("modelfile-model") == 16384
        assert client.get_model_context_length("missing-model") is None


def test_num_ctx_zero_omits_options_and_positive_sends_options(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify num_ctx == 0 respects Ollama model default without injecting RoPE ceiling into options."""
    recorded_payloads: list[dict[str, object]] = []

    def mock_get(url: str, timeout: float) -> httpx.Response:
        del url, timeout
        return httpx.Response(200, json={"models": [{"name": "embeddinggemma-2:latest"}]})

    def mock_post(url: str, json: dict[str, object], timeout: float) -> httpx.Response:
        del url, timeout
        recorded_payloads.append(json)
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2]] if json.get("input") else []})

    with OllamaClient() as client_default:
        monkeypatch.setattr(client_default._client, "get", mock_get)
        monkeypatch.setattr(client_default._client, "post", mock_post)

        assert client_default.num_ctx == 0
        client_default.preload("embeddinggemma-2")
        assert recorded_payloads[-1] == {"model": "embeddinggemma-2:latest", "input": []}

        client_default.embed("embeddinggemma-2", ["hello"])
        assert "options" not in recorded_payloads[-1]

        client_default.preload("embeddinggemma-2", num_ctx=8192)
        assert recorded_payloads[-1] == {
            "model": "embeddinggemma-2:latest",
            "input": [],
            "options": {"num_ctx": 8192},
        }

        client_default.unload("embeddinggemma-2")
        assert recorded_payloads[-1] == {
            "model": "embeddinggemma-2:latest",
            "input": [],
            "keep_alive": 0,
        }

    with OllamaClient(num_ctx=8192) as client_ctx:
        monkeypatch.setattr(client_ctx._client, "get", mock_get)
        monkeypatch.setattr(client_ctx._client, "post", mock_post)

        client_ctx.embed("embeddinggemma-2", ["hello"])
        assert recorded_payloads[-1]["options"] == {"num_ctx": 8192}

        client_ctx.embed("embeddinggemma-2", ["hello"], num_ctx=4096)
        assert recorded_payloads[-1]["options"] == {"num_ctx": 4096}

        client_ctx.embed("embeddinggemma-2", ["hello"], num_ctx=0)
        assert "options" not in recorded_payloads[-1]


def test_resolve_model_name_caches_lookup_and_skips_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with OllamaClient() as client:
        tags_calls = 0

        def mock_get(url: str, timeout: float) -> httpx.Response:
            del url, timeout
            nonlocal tags_calls
            tags_calls += 1
            if tags_calls == 1:
                raise httpx.ConnectError("Connection refused")
            return httpx.Response(200, json={"models": [{"name": "embeddinggemma-2:latest"}]})

        monkeypatch.setattr(client._client, "get", mock_get)

        assert client.resolve_model_name("embeddinggemma-2") == "embeddinggemma-2"
        assert ("http://localhost:11434", "embeddinggemma-2") not in client._resolved_model_cache
        assert client.resolve_model_name("embeddinggemma-2") == "embeddinggemma-2:latest"
        assert client.resolve_model_name("embeddinggemma-2") == "embeddinggemma-2:latest"
        assert tags_calls == 2
