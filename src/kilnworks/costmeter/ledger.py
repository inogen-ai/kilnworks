COST_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cost_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INT NOT NULL,
    output_tokens INT NOT NULL DEFAULT 0,
    context TEXT NOT NULL DEFAULT '',
    user_id TEXT
);
CREATE INDEX IF NOT EXISTS cost_events_occurred_idx ON cost_events (occurred_at);
"""


class PgCostLedger:
    """Postgres-backed token-spend ledger. Standalone by design: no kilnworks imports."""

    def __init__(self, conn):
        self._conn = conn

    def ensure_schema(self) -> None:
        self._conn.execute(COST_SCHEMA_SQL)

    def record(self, event) -> None:
        self._conn.execute(
            """INSERT INTO cost_events
                   (kind, model, input_tokens, output_tokens, context, user_id)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                event.kind,
                event.model,
                event.input_tokens,
                event.output_tokens,
                event.context,
                event.user_id,
            ),
        )

    def summary(self) -> list[tuple[str, str, int, int, int]]:
        return self._conn.execute(
            """SELECT kind, model, count(*)::int,
                      sum(input_tokens)::bigint, sum(output_tokens)::bigint
               FROM cost_events
               GROUP BY kind, model
               ORDER BY kind, model"""
        ).fetchall()
