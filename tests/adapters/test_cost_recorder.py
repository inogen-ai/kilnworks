from kilnworks.adapters.cost import LedgerCostRecorder
from kilnworks.costmeter import PgCostLedger


def test_recorder_writes_through_to_ledger(conn):
    ledger = PgCostLedger(conn)
    recorder = LedgerCostRecorder(ledger)
    recorder.record_cost("chat", "gpt-4o-mini", 10, 5, "query")
    assert ledger.summary() == [("chat", "gpt-4o-mini", 1, 10, 5)]


def test_recorder_passes_user_id(conn):
    ledger = PgCostLedger(conn)
    LedgerCostRecorder(ledger).record_cost("chat", "m", 1, 1, "query", user_id="u-1")
    row = conn.execute("SELECT user_id FROM cost_events").fetchone()
    assert row == ("u-1",)
