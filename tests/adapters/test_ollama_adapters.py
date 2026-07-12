import json
from types import SimpleNamespace

import httpx
import pytest

from kilnworks.adapters.embedders.ollama import OllamaEmbedder
from kilnworks.adapters.llm.ollama import OllamaChat
from kilnworks.core.errors import ProviderError
from kilnworks.core.models import Completion


def _client(handler):
    return httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))


def test_chat_complete_happy_path():
    def handler(request):
        payload = json.loads(request.content)
        assert payload == {
            "model": "llama3.2",
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "usr"},
            ],
            "stream": False,
            "options": {"num_ctx": 8192},
        }
        return httpx.Response(
            200,
            json={
                "message": {"content": "the answer [1]"},
                "model": "llama3.2",
                "prompt_eval_count": 11,
                "eval_count": 7,
            },
        )

    chat = OllamaChat(client=_client(handler))
    completion = chat.complete("sys", "usr")
    assert completion.text == "the answer [1]"
    assert completion.model == "llama3.2"
    assert completion.input_tokens == 11
    assert completion.output_tokens == 7


def test_chat_complete_missing_counts_default_to_zero():
    def handler(request):
        return httpx.Response(200, json={"message": {"content": "hi"}, "model": "llama3.2"})

    completion = OllamaChat(client=_client(handler)).complete("s", "u")
    assert completion.input_tokens == 0 and completion.output_tokens == 0
    assert completion.text == "hi"


def test_chat_stream_yields_deltas_then_completion():
    lines = [
        {"message": {"content": "Hello "}, "done": False},
        {"message": {"content": "world"}, "done": False},
        {"done": True, "model": "llama3.2", "prompt_eval_count": 9, "eval_count": 2},
    ]
    body = "\n".join(json.dumps(line) for line in lines) + "\n"

    def handler(request):
        payload = json.loads(request.content)
        assert payload["stream"] is True
        assert payload["options"] == {"num_ctx": 8192}
        return httpx.Response(200, content=body)

    events = list(OllamaChat(client=_client(handler)).stream("s", "u"))
    *deltas, final = events
    assert deltas == ["Hello ", "world"]
    assert isinstance(final, Completion)
    assert final.text == "Hello world"
    assert final.model == "llama3.2"
    assert final.input_tokens == 9 and final.output_tokens == 2


def test_chat_custom_num_ctx_is_sent_in_options():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["options"] == {"num_ctx": 32768}
        return httpx.Response(200, json={"message": {"content": "ok"}, "model": "llama3.2"})

    completion = OllamaChat(client=_client(handler), num_ctx=32768).complete("s", "u")
    assert completion.text == "ok"


def test_chat_default_client_uses_configured_timeout():
    chat = OllamaChat(base_url="http://ollama.test", timeout=42.0)
    timeout = chat._client.timeout
    assert timeout.read == 42.0
    assert timeout.connect == 10.0


def test_embedder_default_client_uses_configured_timeout():
    embedder = OllamaEmbedder(base_url="http://ollama.test", timeout=42.0)
    timeout = embedder._client.timeout
    assert timeout.read == 42.0
    assert timeout.connect == 10.0


def test_embedder_happy_path():
    def handler(request):
        payload = json.loads(request.content)
        assert payload == {"model": "nomic-embed-text", "input": ["a", "b"]}
        return httpx.Response(
            200,
            json={
                "embeddings": [[0.1] * 768, [0.2] * 768],
                "prompt_eval_count": 5,
            },
        )

    embedder = OllamaEmbedder(client=_client(handler))
    batch = embedder.embed(["a", "b"])
    assert len(batch.vectors) == 2 and len(batch.vectors[0]) == 768
    assert batch.total_tokens == 5
    assert embedder.dimension == 768
    assert embedder.model_name == "nomic-embed-text"


def test_embedder_dimension_mismatch_raises_value_error():
    def handler(request):
        return httpx.Response(200, json={"embeddings": [[0.1] * 512], "prompt_eval_count": 1})

    embedder = OllamaEmbedder(client=_client(handler), dimension=768)
    with pytest.raises(ValueError) as excinfo:
        embedder.embed(["a"])
    message = str(excinfo.value)
    assert "768" in message and "512" in message
    assert "KILNWORKS_EMBEDDING_DIMENSIONS" in message
    assert "init-db" in message


def test_chat_500_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr("kilnworks.core.retry.time", SimpleNamespace(sleep=lambda s: None))
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(500, text="server error")
        return httpx.Response(
            200,
            json={
                "message": {"content": "recovered"},
                "model": "llama3.2",
                "prompt_eval_count": 1,
                "eval_count": 1,
            },
        )

    completion = OllamaChat(client=_client(handler)).complete("s", "u")
    assert completion.text == "recovered"
    assert attempts["n"] == 2


def test_chat_404_is_immediate_provider_error_no_retry():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(404, text="not found")

    with pytest.raises(ProviderError) as excinfo:
        OllamaChat(client=_client(handler)).complete("s", "u")
    assert excinfo.value.provider == "ollama"
    assert attempts["n"] == 1


def test_chat_connect_error_exhausts_to_provider_error(monkeypatch):
    monkeypatch.setattr("kilnworks.core.retry.time", SimpleNamespace(sleep=lambda s: None))

    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ProviderError) as excinfo:
        OllamaChat(client=_client(handler)).complete("s", "u")
    assert excinfo.value.provider == "ollama"


def test_embedder_connect_error_exhausts_to_provider_error(monkeypatch):
    monkeypatch.setattr("kilnworks.core.retry.time", SimpleNamespace(sleep=lambda s: None))

    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ProviderError) as excinfo:
        OllamaEmbedder(client=_client(handler)).embed(["x"])
    assert excinfo.value.provider == "ollama"


def test_chat_invalid_json_response_is_provider_error():
    def handler(request):
        return httpx.Response(200, content=b"<html>totally not json</html>")

    with pytest.raises(ProviderError) as excinfo:
        OllamaChat(client=_client(handler)).complete("s", "u")
    assert excinfo.value.provider == "ollama"
    assert "invalid response" in str(excinfo.value)
