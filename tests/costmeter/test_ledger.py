from kilnworks.costmeter import CostEvent, PgCostLedger


def test_record_and_summary_roundtrip(conn):
    conn.execute("TRUNCATE cost_events")
    ledger = PgCostLedger(conn)
    ledger.ensure_schema()  # idempotent on top of init_db
    ledger.record(CostEvent(kind="chat", model="gpt-4o-mini", input_tokens=10, output_tokens=5))
    ledger.record(CostEvent(kind="chat", model="gpt-4o-mini", input_tokens=7, output_tokens=3))
    ledger.record(
        CostEvent(kind="embedding", model="text-embedding-3-small", input_tokens=42,
                  context="ingest")
    )
    assert ledger.summary() == [
        ("chat", "gpt-4o-mini", 2, 17, 8),
        ("embedding", "text-embedding-3-small", 1, 42, 0),
    ]


def test_user_id_and_context_are_stored(conn):
    conn.execute("TRUNCATE cost_events")
    ledger = PgCostLedger(conn)
    ledger.record(
        CostEvent(kind="chat", model="m", input_tokens=1, output_tokens=1,
                  context="query", user_id="user-1")
    )
    row = conn.execute("SELECT context, user_id FROM cost_events").fetchone()
    assert row == ("query", "user-1")
