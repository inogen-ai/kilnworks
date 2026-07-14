import time
from uuid import uuid4

import pytest

from kilnworks.adapters.connectors.fake import FakeConnector
from kilnworks.adapters.embedders.fake import FakeEmbedder
from kilnworks.adapters.llm.fake import FakeLLM
from kilnworks.core.errors import ProviderError
from kilnworks.core.models import CONNECTOR_STATUS_DOWN, ConnectorResult, RetrievedChunk
from kilnworks.core.query import NO_ANSWER_TEXT, QueryService, format_context


class StubIndex:
    def __init__(self, results):
        self._results = results
        self.searches = []

    def upsert_chunks(self, chunks, embeddings):
        raise NotImplementedError

    def search(self, embedding, principals, limit=8, source_ids=None):
        self.searches.append((principals, limit, source_ids))
        return self._results


def _hit(text, title="doc", heading_path=None):
    return RetrievedChunk(
        document_id=uuid4(),
        ordinal=0,
        text=text,
        heading_path=heading_path or [],
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
    assert index.searches == [(("hr", "public"), 3, None)]


def test_context_blocks_are_numbered_in_prompt():
    index = StubIndex([_hit("alpha"), _hit("beta", title="b")])
    llm = FakeLLM()
    QueryService(FakeEmbedder(), index, llm).ask("q")
    _, user_prompt = llm.calls[0]
    assert "[1] (doc) alpha" in user_prompt
    assert "[2] (b) beta" in user_prompt
    assert user_prompt.rstrip().endswith("Question: q")


def test_format_context_includes_heading_path():
    results = [_hit("alpha", title="doc", heading_path=["Firing temperatures"])]
    assert "[1] (doc › Firing temperatures) alpha" in format_context(results)


def test_citation_carries_heading_path():
    index = StubIndex([_hit("kilns fire hot", heading_path=["Firing temperatures"])])
    llm = FakeLLM(reply="Hot [1].")
    answer = QueryService(FakeEmbedder(), index, llm).ask("q")
    assert answer.citations[0].heading_path == ["Firing temperatures"]


def test_citation_locator_from_line_start_timestamp():
    index = StubIndex([_hit("[02:15] welcome to the call")])
    llm = FakeLLM(reply="Said hi [1].")
    answer = QueryService(FakeEmbedder(), index, llm).ask("q")
    assert answer.citations[0].locator == "02:15"


def test_citation_locator_from_hour_timestamp():
    index = StubIndex([_hit("[1:02:15] later in the call")])
    llm = FakeLLM(reply="Later [1].")
    answer = QueryService(FakeEmbedder(), index, llm).ask("q")
    assert answer.citations[0].locator == "1:02:15"


def test_citation_locator_none_for_normal_doc():
    index = StubIndex([_hit("kilns fire at 1300 degrees")])
    llm = FakeLLM(reply="Hot [1].")
    answer = QueryService(FakeEmbedder(), index, llm).ask("q")
    assert answer.citations[0].locator is None


def test_citation_locator_none_for_mid_sentence_bracket():
    index = StubIndex([_hit("call started, see note [12:34] for details")])
    llm = FakeLLM(reply="See it [1].")
    answer = QueryService(FakeEmbedder(), index, llm).ask("q")
    assert answer.citations[0].locator is None


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
    assert index.searches == [(("hr",), 3, None)]
    assert cost.events == [("embedding", "fake-embedder", 2, 0, "query", "u-1")]


def test_retrieve_passes_source_ids_to_index():
    source_a, source_b = uuid4(), uuid4()
    index = StubIndex([_hit("a")])
    service = QueryService(FakeEmbedder(), index, FakeLLM())
    service.retrieve("q", source_ids=[source_a, source_b])
    assert index.searches == [(("public",), 8, [source_a, source_b])]


def test_ask_forwards_source_ids_and_connectors_to_retrieve():
    source_a = uuid4()
    index = StubIndex([_hit("a")])
    service = QueryService(FakeEmbedder(), index, FakeLLM())
    service.ask("q", source_ids=[source_a], connectors=["slack"])
    assert index.searches == [(("public",), 8, [source_a])]


def test_ask_stream_forwards_source_ids_and_connectors_to_retrieve():
    source_a = uuid4()
    index = StubIndex([_hit("a")])
    service = QueryService(FakeEmbedder(), index, FakeLLM())
    list(service.ask_stream("q", source_ids=[source_a], connectors=["slack"]))
    assert index.searches == [(("public",), 8, [source_a])]


class FakeRegistry:
    """Tiny fake for the C3 `connector_registry` interface: `allowed_for(principals)`."""

    def __init__(self, connectors):
        self._connectors = connectors

    def allowed_for(self, principals):
        return list(self._connectors)


def test_connector_results_merged_as_citable_chunks():
    index = StubIndex([_hit("local passage")])
    connector = FakeConnector(
        name="slack",
        results=[
            ConnectorResult(
                title="Thread 1", text="slack text 1", link="https://s/1", connector="slack"
            ),
            ConnectorResult(title="Thread 2", text="slack text 2", connector="slack"),
        ],
    )
    registry = FakeRegistry([connector])
    llm = FakeLLM(reply="Local [1]. Slack says [2] and [3].")
    service = QueryService(FakeEmbedder(), index, llm, connector_registry=registry)

    results = service.retrieve("q", connectors=["slack"])

    assert [r.title for r in results] == ["doc", "slack: Thread 1", "slack: Thread 2"]
    assert results[1].source_uri == "https://s/1"
    assert results[1].text == "slack text 1"
    assert results[2].source_uri == "slack"  # no link -> falls back to connector name
    # Connector selection is by allow-list membership alone; the query path never
    # spawns a second connector process just to probe status().
    assert connector.status_calls == 0

    answer = service.ask("q", connectors=["slack"])
    assert [c.index for c in answer.citations] == [1, 2, 3]
    assert answer.citations[1].title == "slack: Thread 1"
    assert answer.citations[2].title == "slack: Thread 2"


def test_slow_connector_is_skipped_not_fatal():
    index = StubIndex([_hit("local passage")])
    slow = FakeConnector(name="slow", delay=1.0, results=[
        ConnectorResult(title="never", text="never", connector="slow"),
    ])
    registry = FakeRegistry([slow])
    service = QueryService(
        FakeEmbedder(), index, FakeLLM(reply="Local [1]."), connector_registry=registry,
        connector_timeout=0.05,
    )

    results = service.retrieve("q", connectors=["slow"])

    assert [r.title for r in results] == ["doc"]
    answer = service.ask("q", connectors=["slow"])
    assert answer.text == "Local [1]."


def test_slow_connector_does_not_block_retrieve_wall_clock():
    """A connector far slower than connector_timeout must not stretch retrieve()'s
    wall-clock time out to the connector's delay. Bounded by a shared deadline of
    ~connector_timeout, not by ThreadPoolExecutor's default wait-for-all-threads
    shutdown behavior.
    """
    index = StubIndex([_hit("local passage")])
    slow = FakeConnector(name="slow", delay=1.0, results=[
        ConnectorResult(title="never", text="never", connector="slow"),
    ])
    registry = FakeRegistry([slow])
    service = QueryService(
        FakeEmbedder(), index, FakeLLM(reply="Local [1]."), connector_registry=registry,
        connector_timeout=0.1,
    )

    start = time.monotonic()
    results = service.retrieve("q", connectors=["slow"])
    elapsed = time.monotonic() - start

    assert [r.title for r in results] == ["doc"]
    msg = f"retrieve() took {elapsed:.3f}s, expected well under the 1.0s connector delay"
    assert elapsed < 0.5, msg


def test_raising_connector_is_skipped():
    index = StubIndex([_hit("local passage")])
    ok = FakeConnector(
        name="ok",
        results=[ConnectorResult(title="Good", text="good text", connector="ok")],
    )
    bad = FakeConnector(name="bad", raises=RuntimeError("connector exploded"))
    registry = FakeRegistry([ok, bad])
    service = QueryService(
        FakeEmbedder(), index, FakeLLM(reply="ok"), connector_registry=registry
    )

    results = service.retrieve("q", connectors=["ok", "bad"])

    assert [r.title for r in results] == ["doc", "ok: Good"]
    answer = service.ask("q", connectors=["ok", "bad"])
    assert answer.text == "ok"


def test_connectors_not_selected_is_pure_local():
    index = StubIndex([_hit("local passage")])
    connector = FakeConnector(
        name="slack",
        results=[ConnectorResult(title="Thread", text="text", connector="slack")],
    )
    registry = FakeRegistry([connector])
    service = QueryService(FakeEmbedder(), index, FakeLLM(), connector_registry=registry)

    results_none = service.retrieve("q", connectors=None)
    results_empty = service.retrieve("q", connectors=[])

    assert [r.title for r in results_none] == ["doc"]
    assert [r.title for r in results_empty] == ["doc"]
    assert connector.calls == []

    # A connector not in the selected names is never queried, even when others are.
    other = FakeConnector(
        name="other",
        results=[ConnectorResult(title="X", text="x", connector="other")],
    )
    registry2 = FakeRegistry([connector, other])
    service2 = QueryService(FakeEmbedder(), index, FakeLLM(), connector_registry=registry2)
    service2.retrieve("q", connectors=["slack"])
    assert other.calls == []


def test_down_connector_excluded_via_failing_search_not_status_precheck():
    """A connector self-reporting DOWN status is no longer pre-filtered by a
    status() gate; it is only excluded when its search() actually fails, via the
    existing graceful-degradation handling. status() must not be called at all on
    the query path (that gate spawned every connector's server twice per query).
    """
    index = StubIndex([_hit("local passage")])
    down = FakeConnector(
        name="down", status=CONNECTOR_STATUS_DOWN, raises=RuntimeError("connector is down")
    )
    registry = FakeRegistry([down])
    service = QueryService(FakeEmbedder(), index, FakeLLM(reply="ok"), connector_registry=registry)

    results = service.retrieve("q", connectors=["down"])

    assert [r.title for r in results] == ["doc"]
    assert down.calls  # search() was attempted...
    assert down.status_calls == 0  # ...but status() never was


def test_unallowed_connector_name_ignored():
    index = StubIndex([_hit("local passage")])
    registry = FakeRegistry([])  # allowed_for returns nothing -> "slack" is not allowed
    service = QueryService(FakeEmbedder(), index, FakeLLM(), connector_registry=registry)

    results = service.retrieve("q", connectors=["slack"])

    assert [r.title for r in results] == ["doc"]


def test_federated_results_are_capped_at_connector_context_cap():
    """Even with connectors selected that together return more results than the
    cap, the merged federated additions must be capped -- not grow unbounded with
    the number of selected connectors or connector_result_limit."""
    index = StubIndex([_hit("local passage")])
    many_results = [
        ConnectorResult(title=f"R{i}", text=f"text {i}", connector="c1") for i in range(10)
    ]
    connector = FakeConnector(name="c1", results=many_results)
    registry = FakeRegistry([connector])
    service = QueryService(
        FakeEmbedder(),
        index,
        FakeLLM(),
        connector_registry=registry,
        connector_result_limit=10,
        connector_context_cap=3,
    )

    results = service.retrieve("q", connectors=["c1"])

    federated = [r for r in results if r.title.startswith("c1:")]
    assert len(federated) == 3
