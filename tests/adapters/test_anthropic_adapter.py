from types import SimpleNamespace

import anthropic
import httpx
import pytest

from kilnworks.adapters.llm.anthropic import AnthropicChat
from kilnworks.core.errors import ProviderError
from kilnworks.core.models import Completion


def _response(text="the answer [1]", model="claude-opus-4-8-resolved"):
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        usage=SimpleNamespace(input_tokens=11, output_tokens=7),
        model=model,
    )


class StubMessages:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return _response()


def test_complete_sends_system_and_reads_usage():
    stub = SimpleNamespace(messages=StubMessages())
    chat = AnthropicChat(api_key="k", client=stub)
    completion = chat.complete("sys", "usr")
    assert completion.text == "the answer [1]"          # thinking block excluded
    assert completion.model == "claude-opus-4-8-resolved"
    assert completion.input_tokens == 11 and completion.output_tokens == 7
    kwargs = stub.messages.kwargs
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["system"] == "sys"
    assert kwargs["messages"] == [{"role": "user", "content": "usr"}]
    assert kwargs["max_tokens"] == 2048
    assert "thinking" not in kwargs                     # deliberately omitted


def test_complete_tolerates_missing_usage():
    stub = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kw: SimpleNamespace(
                content=[SimpleNamespace(type="text", text="hi")],
                usage=None,
                model=None,
            )
        )
    )
    completion = AnthropicChat(api_key="k", client=stub).complete("s", "u")
    assert completion.input_tokens == 0 and completion.output_tokens == 0
    assert completion.model == "claude-opus-4-8"        # falls back to requested


class FakeStreamContext:
    def __init__(self, chunks, final):
        self.text_stream = iter(chunks)
        self._final = final

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get_final_message(self):
        return self._final


def test_stream_yields_deltas_then_completion():
    final = _response(text="Hello world")
    stub = SimpleNamespace(
        messages=SimpleNamespace(stream=lambda **kw: FakeStreamContext(["Hello ", "world"], final))
    )
    events = list(AnthropicChat(api_key="k", client=stub).stream("s", "u"))
    *deltas, completion = events
    assert deltas == ["Hello ", "world"]
    assert isinstance(completion, Completion)
    assert completion.text == "Hello world"
    assert completion.input_tokens == 11


def test_transient_error_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr("kilnworks.core.retry.time", SimpleNamespace(sleep=lambda s: None))
    attempts = {"n": 0}

    def create(**kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://a.test"))
        return _response()

    stub = SimpleNamespace(messages=SimpleNamespace(create=create))
    assert AnthropicChat(api_key="k", client=stub).complete("s", "u").text == "the answer [1]"
    assert attempts["n"] == 2


def test_exhausted_transient_errors_raise_provider_error(monkeypatch):
    monkeypatch.setattr("kilnworks.core.retry.time", SimpleNamespace(sleep=lambda s: None))

    def create(**kwargs):
        raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://a.test"))

    stub = SimpleNamespace(messages=SimpleNamespace(create=create))
    with pytest.raises(ProviderError) as excinfo:
        AnthropicChat(api_key="k", client=stub).complete("s", "u")
    assert excinfo.value.provider == "anthropic"


def test_stream_transient_error_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr("kilnworks.core.retry.time", SimpleNamespace(sleep=lambda s: None))
    attempts = {"n": 0}

    def stream(**kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://a.test"))
        return FakeStreamContext(["clean ", "run"], _response(text="clean run"))

    stub = SimpleNamespace(messages=SimpleNamespace(stream=stream))
    events = list(AnthropicChat(api_key="k", client=stub).stream("s", "u"))
    *deltas, completion = events
    assert deltas == ["clean ", "run"]
    assert completion.text == "clean run"
    assert attempts["n"] == 2
