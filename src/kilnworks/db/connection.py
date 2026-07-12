import psycopg
from pgvector.psycopg import register_vector

from kilnworks.costmeter import PgCostLedger
from kilnworks.db.schema import schema_sql


def connect(database_url: str) -> psycopg.Connection:
    conn = psycopg.connect(database_url, autocommit=True)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    return conn


def init_db(conn: psycopg.Connection, dimensions: int = 1536) -> None:
    conn.execute(schema_sql(dimensions))
    PgCostLedger(conn).ensure_schema()
