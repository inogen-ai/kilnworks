from types import SimpleNamespace

import httpx
import openai
import pytest

from kilnworks.adapters.embedders.openai import OpenAIEmbedder
from kilnworks.adapters.llm.openai import OpenAIChat
from kilnworks.core.errors import ProviderError
from kilnworks.core.models import Completion


class StubEmbeddings:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        width = kwargs.get("dimensions", 1536)
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1] * width) for _ in kwargs["input"]],
            usage=SimpleNamespace(total_tokens=7),
        )


class StubCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        message = SimpleNamespace(content="the answer [1]")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=5),
        )


def test_embedder_calls_openai_with_model_and_texts():
    stub = SimpleNamespace(embeddings=StubEmbeddings())
    embedder = OpenAIEmbedder(api_key="k", client=stub)
    batch = embedder.embed(["a", "b"])
    assert stub.embeddings.kwargs == {"model": "text-embedding-3-small", "input": ["a", "b"]}
    assert len(batch.vectors) == 2 and len(batch.vectors[0]) == 1536
    assert batch.total_tokens == 7


def test_embedder_omits_dimensions_kwarg_at_default():
    stub = SimpleNamespace(embeddings=StubEmbeddings())
    OpenAIEmbedder(api_key="k", client=stub).embed(["a"])
    assert "dimensions" not in stub.embeddings.kwargs


def test_embedder_passes_dimensions_kwarg_when_non_default():
    stub = SimpleNamespace(embeddings=StubEmbeddings())
    OpenAIEmbedder(api_key="k", client=stub, dimension=3072).embed(["a"])
    assert stub.embeddings.kwargs["dimensions"] == 3072


def test_embedder_dimension_mismatch_raises_value_error():
    class WideEmbeddings:
        def create(self, **kwargs):
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[0.1] * 3072) for _ in kwargs["input"]],
                usage=SimpleNamespace(total_tokens=7),
            )

    stub = SimpleNamespace(embeddings=WideEmbeddings())
    embedder = OpenAIEmbedder(api_key="k", client=stub, dimension=1536)
    with pytest.raises(ValueError) as excinfo:
        embedder.embed(["a"])
    message = str(excinfo.value)
    assert "1536" in message and "3072" in message
    assert "KILNWORKS_EMBEDDING_DIMENSIONS" in message
    assert "init-db" in message


def test_chat_sends_system_and_user_messages():
    stub = SimpleNamespace(chat=SimpleNamespace(completions=StubCompletions()))
    chat = OpenAIChat(api_key="k", client=stub)
    completion = chat.complete("sys", "usr")
    assert completion.text == "the answer [1]"
    assert completion.model == "gpt-4o-mini"
    assert completion.input_tokens == 12 and completion.output_tokens == 5
    assert stub.chat.completions.kwargs["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]


def test_chat_returns_empty_string_for_null_content():
    stub = SimpleNamespace(chat=SimpleNamespace(completions=StubCompletions()))
    stub.chat.completions.create = lambda **kw: SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0),
    )
    assert OpenAIChat(api_key="k", client=stub).complete("s", "u").text == ""


def _connection_error():
    return openai.APIConnectionError(request=httpx.Request("POST", "https://api.test"))


def test_transient_sdk_error_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr("kilnworks.core.retry.time", SimpleNamespace(sleep=lambda s: None))
    attempts = {"n": 0}

    def create(**kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise _connection_error()
        message = SimpleNamespace(content="recovered [1]")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    stub = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    assert OpenAIChat(api_key="k", client=stub).complete("s", "u").text == "recovered [1]"
    assert attempts["n"] == 2


def test_exhausted_transient_errors_raise_provider_error(monkeypatch):
    monkeypatch.setattr("kilnworks.core.retry.time", SimpleNamespace(sleep=lambda s: None))

    def create(**kwargs):
        raise _connection_error()

    stub = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    with pytest.raises(ProviderError) as excinfo:
        OpenAIEmbedder(api_key="k", client=stub).embed(["x"])
    assert excinfo.value.provider == "openai"


def test_fake_llm_stream_yields_deltas_then_completion():
    from kilnworks.adapters.llm.fake import FakeLLM

    llm = FakeLLM(reply="alpha beta [1]")
    events = list(llm.stream("sys", "usr"))
    *deltas, final = events
    assert all(isinstance(d, str) for d in deltas)
    assert isinstance(final, Completion)
    assert "".join(deltas) == "alpha beta [1]"
    assert final.text == "alpha beta [1]"
    assert llm.calls == [("sys", "usr")]


def _chunk(content=None, model=None, usage=None):
    delta = SimpleNamespace(content=content)
    choices = [SimpleNamespace(delta=delta)] if content is not None else []
    return SimpleNamespace(choices=choices, model=model, usage=usage)


def test_openai_stream_concatenates_and_reads_usage():
    def create(**kwargs):
        assert kwargs["stream"] is True
        assert kwargs["stream_options"] == {"include_usage": True}
        return iter([
            _chunk(content="Hello ", model="gpt-4o-mini-2024-07-18"),
            _chunk(content="world"),
            _chunk(usage=SimpleNamespace(prompt_tokens=9, completion_tokens=2)),
        ])

    stub = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    events = list(OpenAIChat(api_key="k", client=stub).stream("s", "u"))
    *deltas, final = events
    assert deltas == ["Hello ", "world"]
    assert final.text == "Hello world"
    assert final.model == "gpt-4o-mini-2024-07-18"
    assert final.input_tokens == 9 and final.output_tokens == 2


def test_complete_tolerates_missing_usage_and_records_resolved_model():
    stub = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))],
            usage=None,
            model="gpt-4o-mini-2024-07-18",
        )
    )))
    completion = OpenAIChat(api_key="k", client=stub).complete("s", "u")
    assert completion.input_tokens == 0 and completion.output_tokens == 0
    assert completion.model == "gpt-4o-mini-2024-07-18"


def test_stream_transient_error_is_retried_without_duplicate_deltas(monkeypatch):
    monkeypatch.setattr("kilnworks.core.retry.time", SimpleNamespace(sleep=lambda s: None))
    attempts = {"n": 0}

    def create(**kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            def broken():
                yield _chunk(content="partial ")
                raise _connection_error()
            return broken()
        return iter([
            _chunk(content="clean ", model="m-resolved"),
            _chunk(content="run"),
            _chunk(usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2)),
        ])

    stub = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    events = list(OpenAIChat(api_key="k", client=stub).stream("s", "u"))
    *deltas, final = events
    assert deltas == ["clean ", "run"]          # no "partial " leaked from attempt 1
    assert final.text == "clean run"
    assert attempts["n"] == 2
