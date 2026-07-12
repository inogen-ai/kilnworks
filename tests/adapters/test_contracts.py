"""Cross-provider contract tests.

These parametrize over adapter *factories* (each a zero-arg callable returning a
ready adapter backed by stubs / httpx.MockTransport, adapted from the patterns in
test_openai_adapters.py, test_anthropic_adapter.py, and test_ollama_adapters.py) and
pin the interface every LLMProvider / Embedder implementation must satisfy, so a new
adapter that violates it fails here rather than at call sites deep in the app.
"""

import json
from types import SimpleNamespace

import httpx
import pytest

from kilnworks.adapters.embedders.fake import FakeEmbedder
from kilnworks.adapters.embedders.ollama import OllamaEmbedder
from kilnworks.adapters.embedders.openai import OpenAIEmbedder
from kilnworks.adapters.llm.anthropic import AnthropicChat
from kilnworks.adapters.llm.fake import FakeLLM
from kilnworks.adapters.llm.ollama import OllamaChat
from kilnworks.adapters.llm.openai import OpenAIChat
from kilnworks.core.models import Completion, EmbeddingBatch

# ---------------------------------------------------------------------------
# Embedder factories
# ---------------------------------------------------------------------------


def _openai_embedder():
    class StubEmbeddings:
        def create(self, **kwargs):
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[0.1] * 1536) for _ in kwargs["input"]],
                usage=SimpleNamespace(total_tokens=7),
            )

    client = SimpleNamespace(embeddings=StubEmbeddings())
    return OpenAIEmbedder(api_key="k", client=client)


def _ollama_embedder():
    def handler(request):
        payload = json.loads(request.content)
        vectors = [[0.1] * 768 for _ in payload["input"]]
        return httpx.Response(200, json={"embeddings": vectors, "prompt_eval_count": 5})

    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    return OllamaEmbedder(client=client)


EMBEDDER_FACTORIES = [
    pytest.param(FakeEmbedder, id="fake"),
    pytest.param(_openai_embedder, id="openai"),
    pytest.param(_ollama_embedder, id="ollama"),
]


@pytest.mark.parametrize("make_embedder", EMBEDDER_FACTORIES)
def test_embedder_contract(make_embedder):
    adapter = make_embedder()

    batch = adapter.embed(["alpha", "beta"])

    assert isinstance(batch, EmbeddingBatch)
    assert len(batch.vectors) == 2
    assert len(batch.vectors[0]) == adapter.dimension
    assert isinstance(batch.total_tokens, int)
    assert batch.total_tokens >= 0
    assert isinstance(adapter.model_name, str) and adapter.model_name


# ---------------------------------------------------------------------------
# LLM factories
#
# Each stub's reply text is identical whether the adapter is asked to
# complete() or stream(), so the "".join(deltas) == final.text assertion
# below is meaningful rather than a coincidence of two unrelated fixtures.
# ---------------------------------------------------------------------------

_REPLY = "alpha beta [1]"


def _openai_chat():
    def create(**kwargs):
        if kwargs.get("stream"):
            return iter(
                [
                    _openai_chunk(content="alpha ", model="gpt-4o-mini-test"),
                    _openai_chunk(content="beta "),
                    _openai_chunk(content="[1]"),
                    _openai_chunk(usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3)),
                ]
            )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=_REPLY))],
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3),
            model="gpt-4o-mini-test",
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return OpenAIChat(api_key="k", client=client)


def _openai_chunk(content=None, model=None, usage=None):
    delta = SimpleNamespace(content=content)
    choices = [SimpleNamespace(delta=delta)] if content is not None else []
    return SimpleNamespace(choices=choices, model=model, usage=usage)


def _anthropic_chat():
    def message(text):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=5, output_tokens=3),
            model="claude-test",
        )

    class StreamCtx:
        def __init__(self, chunks, final):
            self.text_stream = iter(chunks)
            self._final = final

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get_final_message(self):
            return self._final

    client = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kw: message(_REPLY),
            stream=lambda **kw: StreamCtx(["alpha ", "beta ", "[1]"], message(_REPLY)),
        )
    )
    return AnthropicChat(api_key="k", client=client)


def _ollama_chat():
    def handler(request):
        payload = json.loads(request.content)
        if payload.get("stream"):
            lines = [
                {"message": {"content": "alpha "}, "done": False},
                {"message": {"content": "beta "}, "done": False},
                {"message": {"content": "[1]"}, "done": False},
                {"done": True, "model": "llama3.2-test", "prompt_eval_count": 5, "eval_count": 3},
            ]
            body = "\n".join(json.dumps(line) for line in lines) + "\n"
            return httpx.Response(200, content=body)
        return httpx.Response(
            200,
            json={
                "message": {"content": _REPLY},
                "model": "llama3.2-test",
                "prompt_eval_count": 5,
                "eval_count": 3,
            },
        )

    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    return OllamaChat(client=client)


LLM_FACTORIES = [
    pytest.param(FakeLLM, id="fake"),
    pytest.param(_openai_chat, id="openai"),
    pytest.param(_anthropic_chat, id="anthropic"),
    pytest.param(_ollama_chat, id="ollama"),
]


@pytest.mark.parametrize("make_llm", LLM_FACTORIES)
def test_llm_contract(make_llm):
    adapter = make_llm()

    completion = adapter.complete("sys", "usr")

    assert isinstance(completion, Completion)
    assert isinstance(completion.text, str)
    assert isinstance(completion.model, str) and completion.model
    assert isinstance(completion.input_tokens, int) and completion.input_tokens >= 0
    assert isinstance(completion.output_tokens, int) and completion.output_tokens >= 0

    events = list(adapter.stream("sys", "usr"))
    *deltas, final = events

    assert all(isinstance(delta, str) for delta in deltas)
    assert isinstance(final, Completion)
    assert "".join(deltas) == final.text
