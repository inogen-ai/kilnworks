def test_init_db_is_idempotent_and_creates_tables(conn):
    from kilnworks.db.connection import init_db

    init_db(conn)  # second run must not raise
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    }
    assert {"documents", "chunks"} <= tables


def test_chunks_embedding_column_is_vector_1536(conn):
    row = conn.execute(
        """SELECT atttypmod FROM pg_attribute
           WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"""
    ).fetchone()
    assert row[0] == 1536


def test_chunks_has_nullable_page_column(conn):
    row = conn.execute(
        """SELECT is_nullable FROM information_schema.columns
           WHERE table_name = 'chunks' AND column_name = 'page'"""
    ).fetchone()
    assert row is not None  # column exists
    assert row[0] == "YES"  # nullable


def _chunks_embedding_typmod(conn):
    return conn.execute(
        """SELECT atttypmod FROM pg_attribute
           WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"""
    ).fetchone()[0]


def test_init_db_with_custom_dimensions(pg_url):
    import psycopg

    from kilnworks.db.connection import connect, init_db
    from kilnworks.db.schema import schema_sql

    admin = connect(pg_url)
    try:
        admin.execute("CREATE DATABASE dims768")
    except psycopg.errors.DuplicateDatabase:
        pass
    admin.close()
    conn = connect(pg_url.rsplit("/", 1)[0] + "/dims768")
    try:
        init_db(conn, dimensions=768)
        assert _chunks_embedding_typmod(conn) == 768
    finally:
        # `init_db` uses CREATE TABLE IF NOT EXISTS, so it won't widen an existing
        # 768-dim column back to the suite default (1536) on its own. Drop and
        # recreate the table here so this shared testcontainer database isn't left
        # at a non-default dimension for anything that reuses it later.
        conn.execute("DROP TABLE IF EXISTS chunks")
        conn.execute(schema_sql(1536))
        restored_typmod = _chunks_embedding_typmod(conn)
        conn.close()
    assert restored_typmod == 1536
