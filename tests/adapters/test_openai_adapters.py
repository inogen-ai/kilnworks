from types import SimpleNamespace

import httpx
import openai
import pytest

from kilnworks.adapters.embedders.openai import (
    _MAX_INPUTS_PER_REQUEST,
    _MAX_TOKENS_PER_REQUEST,
    OpenAIEmbedder,
    _sub_batches,
    _token_estimate,
)
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


class RecordingEmbeddings:
    """Records the `input` of every create() call and returns one vector per
    input whose first coordinate encodes the input's leading index, so tests can
    assert both call-splitting and that vectors come back in the original order."""

    def __init__(self, reverse_data=False):
        self.calls = []
        self._reverse_data = reverse_data

    def create(self, **kwargs):
        inputs = list(kwargs["input"])
        self.calls.append(inputs)
        width = kwargs.get("dimensions", 1536)
        data = []
        for position, text in enumerate(inputs):
            marker = int(text.split("|", 1)[0])
            # `index` mirrors the API's per-item field; `embedding[0]` echoes the
            # input's leading marker so tests can assert order end to end.
            data.append(SimpleNamespace(
                index=position, embedding=[float(marker)] + [0.0] * (width - 1)
            ))
        if self._reverse_data:
            data.reverse()  # exercise the index-based re-sort in _embed_once
        return SimpleNamespace(data=data, usage=SimpleNamespace(total_tokens=len(inputs)))


def _indexed_texts(count, chars):
    # "<index>|xxxx...": the marker lets RecordingEmbeddings map each vector back
    # to its input; the padding gives each input a controllable character length.
    return [f"{i}|" + "x" * chars for i in range(count)]


def test_small_input_stays_a_single_request():
    stub = SimpleNamespace(embeddings=RecordingEmbeddings())
    OpenAIEmbedder(api_key="k", client=stub).embed(_indexed_texts(2, 1))
    assert len(stub.embeddings.calls) == 1  # no needless splitting


def test_embed_splits_when_token_budget_exceeded_and_preserves_order():
    # Each ~1200-byte text estimates to ~1200 tokens; size the corpus to need >1 request.
    chars = 1200
    count = (_MAX_TOKENS_PER_REQUEST // chars) + 50  # comfortably over one request
    stub = SimpleNamespace(embeddings=RecordingEmbeddings())
    batch = OpenAIEmbedder(api_key="k", client=stub).embed(_indexed_texts(count, chars))

    assert len(stub.embeddings.calls) >= 2  # actually split
    # No individual request exceeded the token budget. `_token_estimate` is UTF-8
    # byte length, a provable UPPER bound on real tokens — so this asserts the
    # real request is safely under 300k, not merely under the estimator's own guess.
    for inputs in stub.embeddings.calls:
        assert sum(_token_estimate(t) for t in inputs) <= _MAX_TOKENS_PER_REQUEST
    # Every vector returned, in the original order (first coord = input marker).
    assert [v[0] for v in batch.vectors] == [float(i) for i in range(count)]
    assert batch.total_tokens == count  # summed across sub-requests


def test_dense_multibyte_text_is_kept_under_the_byte_budget():
    # CJK characters are 3 UTF-8 bytes each and 1-2+ tokens each: a char-count
    # estimate would under-count and 400. Byte counting keeps each request's real
    # token load under the limit. 900 CJK chars ~ 2700 bytes/input.
    cjk = "文" * 900
    texts = [f"{i}|" + cjk for i in range(400)]  # ~1.08MB of bytes, forces splitting
    stub = SimpleNamespace(embeddings=RecordingEmbeddings())
    batch = OpenAIEmbedder(api_key="k", client=stub).embed(texts)

    assert len(stub.embeddings.calls) >= 2
    for inputs in stub.embeddings.calls:
        byte_sum = sum(len(t.encode("utf-8")) for t in inputs)
        assert byte_sum <= _MAX_TOKENS_PER_REQUEST  # tokens <= bytes, so real load is bounded
    assert [v[0] for v in batch.vectors] == [float(i) for i in range(400)]


def test_embed_of_empty_makes_no_request_and_returns_empty_batch():
    stub = SimpleNamespace(embeddings=RecordingEmbeddings())
    batch = OpenAIEmbedder(api_key="k", client=stub).embed([])
    assert stub.embeddings.calls == []  # never hits the API
    assert batch.vectors == [] and batch.total_tokens == 0


def test_response_data_is_reordered_by_index_before_stitching():
    # If the provider ever returns items out of arrival order, the per-item index
    # must still bind each vector to its input — else vectors attach to wrong chunks.
    stub = SimpleNamespace(embeddings=RecordingEmbeddings(reverse_data=True))
    batch = OpenAIEmbedder(api_key="k", client=stub).embed(_indexed_texts(4, 1))
    assert [v[0] for v in batch.vectors] == [0.0, 1.0, 2.0, 3.0]


def test_transient_failure_in_one_group_retries_only_that_group(monkeypatch):
    monkeypatch.setattr("kilnworks.core.retry.time", SimpleNamespace(sleep=lambda s: None))
    calls = []
    fail_once = {"done": False}

    def create(**kwargs):
        inputs = list(kwargs["input"])
        calls.append(inputs)
        # Fail the FIRST time the second group is sent; it must retry that group
        # only, never re-send the first group (which would double-bill/duplicate).
        if len(calls) == 2 and not fail_once["done"]:
            fail_once["done"] = True
            raise _connection_error()
        width = kwargs.get("dimensions", 1536)
        data = [
            SimpleNamespace(
                index=p, embedding=[float(int(t.split("|", 1)[0]))] + [0.0] * (width - 1)
            )
            for p, t in enumerate(inputs)
        ]
        return SimpleNamespace(data=data, usage=SimpleNamespace(total_tokens=len(inputs)))

    stub = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    count = _MAX_INPUTS_PER_REQUEST + 5  # forces exactly two groups
    batch = OpenAIEmbedder(api_key="k", client=stub).embed(_indexed_texts(count, 1))

    first_group = _indexed_texts(_MAX_INPUTS_PER_REQUEST, 1)
    assert calls[0] == first_group  # group 1 sent once...
    assert calls[2] != first_group  # ...and the retry (call 3) re-sent group 2, not group 1
    assert [v[0] for v in batch.vectors] == [float(i) for i in range(count)]


def test_embed_splits_when_input_count_cap_exceeded():
    count = _MAX_INPUTS_PER_REQUEST + 5
    stub = SimpleNamespace(embeddings=RecordingEmbeddings())
    batch = OpenAIEmbedder(api_key="k", client=stub).embed(_indexed_texts(count, 1))

    assert len(stub.embeddings.calls) == 2
    assert all(len(inputs) <= _MAX_INPUTS_PER_REQUEST for inputs in stub.embeddings.calls)
    assert [v[0] for v in batch.vectors] == [float(i) for i in range(count)]


def test_sub_batches_of_empty_is_empty():
    assert _sub_batches([]) == []


def test_sub_batches_keeps_a_single_oversized_input_rather_than_dropping_it():
    huge = "x" * (_MAX_TOKENS_PER_REQUEST * 3)  # bytes, far over one request's budget
    groups = _sub_batches([huge])
    assert groups == [[huge]]  # ships alone; provider fails it loudly, not silently dropped


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
