from uuid import uuid4

import pytest

from kilnworks.adapters.embedders.fake import FakeEmbedder
from kilnworks.adapters.llm.fake import FakeLLM
from kilnworks.core.errors import ProviderError
from kilnworks.core.models import RetrievedChunk
from kilnworks.core.query import NO_ANSWER_TEXT, QueryService


class StubIndex:
    def __init__(self, results):
        self._results = results
        self.searches = []

    def upsert_chunks(self, chunks, embeddings):
        raise NotImplementedError

    def search(self, embedding, principals, limit=8):
        self.searches.append((principals, limit))
        return self._results


def _hit(text, title="doc"):
    return RetrievedChunk(
        document_id=uuid4(),
        ordinal=0,
        text=text,
        source_uri=f"file:///{title}.md",
        title=title,
        score=0.9,
    )


def test_ask_returns_answer_with_parsed_citations():
    index = StubIndex([_hit("kilns fire at 1300 degrees"), _hit("other", title="other")])
    llm = FakeLLM(reply="They fire at 1300 degrees [1]. See also [2] and again [1].")
    service = QueryService(FakeEmbedder(), index, llm)
    answer = service.ask("How hot?")
    assert [c.index for c in answer.citations] == [1, 2]
    assert answer.citations[0].title == "doc"
    assert answer.citations[1].title == "other"
    assert answer.model == "fake"


def test_out_of_range_citations_are_ignored():
    service = QueryService(
        FakeEmbedder(), StubIndex([_hit("a")]), FakeLLM(reply="See [1] and [7].")
    )
    answer = service.ask("q")
    assert [c.index for c in answer.citations] == [1]


def test_empty_retrieval_short_circuits_without_llm_call():
    llm = FakeLLM()
    service = QueryService(FakeEmbedder(), StubIndex([]), llm)
    answer = service.ask("q")
    assert answer.text == NO_ANSWER_TEXT
    assert answer.citations == []
    assert llm.calls == []


def test_principals_are_passed_to_search():
    index = StubIndex([_hit("a")])
    QueryService(FakeEmbedder(), index, FakeLLM()).ask("q", principals=("hr", "public"), limit=3)
    assert index.searches == [(("hr", "public"), 3)]


def test_context_blocks_are_numbered_in_prompt():
    index = StubIndex([_hit("alpha"), _hit("beta", title="b")])
    llm = FakeLLM()
    QueryService(FakeEmbedder(), index, llm).ask("q")
    _, user_prompt = llm.calls[0]
    assert "[1] (doc) alpha" in user_prompt
    assert "[2] (b) beta" in user_prompt
    assert user_prompt.rstrip().endswith("Question: q")


class RecordingCost:
    def __init__(self):
        self.events = []

    def record_cost(self, kind, model, input_tokens, output_tokens, context, user_id=None):
        self.events.append((kind, model, input_tokens, output_tokens, context, user_id))


def test_ask_records_embedding_and_chat_costs():
    cost = RecordingCost()
    service = QueryService(FakeEmbedder(), StubIndex([_hit("a")]), FakeLLM(), cost=cost)
    service.ask("how hot does it get")
    kinds = [event[0] for event in cost.events]
    assert kinds == ["embedding", "chat"]
    embedding_event, chat_event = cost.events
    assert embedding_event[1] == "fake-embedder" and embedding_event[2] > 0
    assert chat_event[1] == "fake" and chat_event[3] > 0
    assert all(event[4] == "query" for event in cost.events)
    assert all(event[5] is None for event in cost.events)


def test_empty_retrieval_records_no_chat_cost():
    cost = RecordingCost()
    QueryService(FakeEmbedder(), StubIndex([]), FakeLLM(), cost=cost).ask("q")
    assert [event[0] for event in cost.events] == ["embedding"]


def test_ask_attributes_costs_to_user():
    cost = RecordingCost()
    service = QueryService(FakeEmbedder(), StubIndex([_hit("a")]), FakeLLM(), cost=cost)
    service.ask("q", user_id="user-42")
    assert all(event[5] == "user-42" for event in cost.events)


def test_ask_stream_yields_deltas_then_answer_with_citations():
    index = StubIndex([_hit("kilns fire hot")])
    service = QueryService(FakeEmbedder(), index, FakeLLM(reply="Very hot [1]"))
    events = list(service.ask_stream("how hot?"))
    *deltas, answer = events
    assert "".join(deltas) == "Very hot [1]"
    assert answer.text == "Very hot [1]"
    assert [c.index for c in answer.citations] == [1]
    assert answer.model == "fake"


def test_ask_stream_empty_retrieval_short_circuits():
    llm = FakeLLM()
    events = list(QueryService(FakeEmbedder(), StubIndex([]), llm).ask_stream("q"))
    assert len(events) == 1
    assert events[0].text == NO_ANSWER_TEXT
    assert llm.calls == []


class _RaisingLLM:
    """A provider whose stream yields one delta then blows up mid-iteration."""

    def stream(self, system, user):
        yield "partial "
        raise ProviderError("fake", "connection reset mid-stream")


def test_ask_stream_propagates_provider_error_mid_iteration():
    index = StubIndex([_hit("a")])
    events = QueryService(FakeEmbedder(), index, _RaisingLLM()).ask_stream("q")
    consumed = [next(events)]
    with pytest.raises(ProviderError):
        next(events)
    assert consumed == ["partial "]


def test_ask_stream_records_costs_with_user():
    cost = RecordingCost()
    service = QueryService(
        FakeEmbedder(), StubIndex([_hit("a")]), FakeLLM(), cost=cost
    )
    list(service.ask_stream("q", user_id="user-7"))
    assert [event[0] for event in cost.events] == ["embedding", "chat"]
    assert all(event[5] == "user-7" for event in cost.events)


def test_retrieve_returns_hits_and_records_embedding_cost():
    cost = RecordingCost()
    index = StubIndex([_hit("kilns are hot")])
    service = QueryService(FakeEmbedder(), index, FakeLLM(), cost=cost)
    results = service.retrieve("how hot?", principals=("hr",), limit=3, user_id="u-1")
    assert [r.text for r in results] == ["kilns are hot"]
    assert index.searches == [(("hr",), 3)]
    assert cost.events == [("embedding", "fake-embedder", 2, 0, "query", "u-1")]
