import json

from tests.api.test_ask_endpoints import _seed_doc, _token
from tests.api.test_auth_endpoints import _register


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    events = []
    for block in raw.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_stream_requires_auth(client):
    assert client.post("/ask/stream", json={"question": "q"}).status_code == 401


def test_stream_emits_deltas_answer_done(client, api_settings):
    _register(api_settings)
    _seed_doc(api_settings, "public knowledge", ["public"])
    headers = {"Authorization": f"Bearer {_token(client)}"}
    with client.stream(
        "POST", "/ask/stream", json={"question": "public knowledge"}, headers=headers
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        raw = "".join(response.iter_text())
    events = _parse_sse(raw)
    kinds = [kind for kind, _ in events]
    assert kinds[0] == "delta" and kinds[-2:] == ["answer", "done"]
    deltas = "".join(payload["text"] for kind, payload in events if kind == "delta")
    answer = next(payload for kind, payload in events if kind == "answer")
    assert deltas == answer["text"]
    assert answer["citations"]


def test_stream_emits_error_event_when_provider_misbehaves(client, api_settings, monkeypatch):
    from kilnworks.adapters.llm.fake import FakeLLM

    def _stream_without_terminal_completion(self, system, user):
        self.calls.append((system, user))
        yield "partial "
        # deliberately omit the terminal Completion event

    monkeypatch.setattr(FakeLLM, "stream", _stream_without_terminal_completion)
    _register(api_settings)
    _seed_doc(api_settings, "public knowledge", ["public"])
    headers = {"Authorization": f"Bearer {_token(client)}"}
    with client.stream(
        "POST", "/ask/stream", json={"question": "public knowledge"}, headers=headers
    ) as response:
        assert response.status_code == 200
        raw = "".join(response.iter_text())
    events = _parse_sse(raw)
    assert events[-1][0] == "error"


def test_stream_disconnect_still_records_chat_cost(api_settings):
    # A real network disconnect can't be simulated deterministically through
    # starlette's TestClient (the ASGI transport races the whole response to
    # completion regardless of how much the test reads back). So we exercise
    # the exact generator the route returns and close it early ourselves,
    # which is precisely what happens when a client drops the connection:
    # Starlette's StreamingResponse.body_iterator gets closed mid-iteration.
    from kilnworks.api.app import _ask_stream_events
    from kilnworks.db.connection import connect, init_db
    from kilnworks.wiring import build_services_with_conn

    conn = connect(api_settings.database_url)
    init_db(conn)
    _register(api_settings)
    _seed_doc(api_settings, "public knowledge", ["public"])
    services = build_services_with_conn(api_settings, conn)

    generator = _ask_stream_events(
        services.query, "public knowledge", ("public",), 8, "user-disconnect"
    )
    next(generator)  # consume exactly one SSE chunk, like a client that reads once then drops
    generator.close()  # simulate the client disconnecting mid-stream

    rows = conn.execute(
        "SELECT kind FROM cost_events WHERE context = 'query' AND user_id = 'user-disconnect'"
    ).fetchall()
    conn.execute("TRUNCATE documents CASCADE")
    conn.execute("TRUNCATE cost_events")
    conn.execute("TRUNCATE users")
    conn.close()
    assert [row[0] for row in rows] == ["embedding", "chat"]
