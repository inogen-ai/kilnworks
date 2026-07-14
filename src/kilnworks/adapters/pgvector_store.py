from collections.abc import Sequence
from uuid import UUID, uuid4

import psycopg
from pgvector import Vector

from kilnworks.core.models import (
    DOC_STATUS_FAILED,
    DOC_STATUS_READY,
    Chunk,
    Document,
    RetrievedChunk,
)

_SEARCH_SQL_BASE = f"""
SELECT c.id, c.document_id, c.ordinal, c.text, c.heading_path, c.acl_tags, c.page,
       d.source_uri, d.title,
       1 - (c.embedding <=> %(embedding)s) AS score
FROM chunks c
JOIN documents d ON d.id = c.document_id
WHERE c.acl_tags && %(principals)s::text[]
  AND d.status = '{DOC_STATUS_READY}'
"""

_SEARCH_SQL = f"""{_SEARCH_SQL_BASE}
ORDER BY c.embedding <=> %(embedding)s
LIMIT %(limit)s
"""

_SEARCH_SQL_SCOPED = f"""{_SEARCH_SQL_BASE}
  AND c.document_id = ANY(%(source_ids)s)
ORDER BY c.embedding <=> %(embedding)s
LIMIT %(limit)s
"""


class PgVectorStore:
    """DocumentStore + VectorIndex backed by Postgres/pgvector."""

    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def upsert_document(self, doc: Document) -> UUID:
        row = self._conn.execute(
            """INSERT INTO documents (id, source_uri, title, acl_tags)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (source_uri) DO UPDATE
                   SET title = EXCLUDED.title, acl_tags = EXCLUDED.acl_tags,
                       status = 'pending', error = NULL
               RETURNING id""",
            (doc.id, doc.source_uri, doc.title, doc.acl_tags),
        ).fetchone()
        doc_id = row[0]
        self._conn.execute("DELETE FROM chunks WHERE document_id = %s", (doc_id,))
        return doc_id

    def mark_document(self, document_id: UUID, status: str, error: str | None = None) -> None:
        self._conn.execute(
            "UPDATE documents SET status = %s, error = %s WHERE id = %s",
            (status, error, document_id),
        )

    def transaction(self):
        return self._conn.transaction()

    def record_ingest_failure(self, source_uri: str, error: str) -> None:
        segment = source_uri.rsplit("/", 1)[-1]
        title = segment.rsplit(".", 1)[0] if "." in segment else segment
        self._conn.execute(
            f"""INSERT INTO documents (id, source_uri, title, status, error)
                VALUES (%(id)s, %(uri)s, %(title)s, '{DOC_STATUS_FAILED}', %(error)s)
                ON CONFLICT (source_uri) DO UPDATE
                    SET error = EXCLUDED.error,
                        status = CASE WHEN documents.status = '{DOC_STATUS_READY}'
                                      THEN documents.status ELSE '{DOC_STATUS_FAILED}' END""",
            {"id": uuid4(), "uri": source_uri, "title": title, "error": error},
        )

    def delete_document(self, document_id: UUID, principals: Sequence[str]) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(
                "DELETE FROM documents WHERE id = %s AND acl_tags && %s::text[]",
                (document_id, list(principals)),
            )
            return cur.rowcount > 0

    def delete_document_chunks(self, document_id: UUID) -> None:
        self._conn.execute("DELETE FROM chunks WHERE document_id = %s", (document_id,))

    def upsert_chunks(
        self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]
    ) -> None:
        with self._conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO chunks
                       (id, document_id, ordinal, text, heading_path, acl_tags, page,
                        embedding)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                [
                    (
                        chunk.id,
                        chunk.document_id,
                        chunk.ordinal,
                        chunk.text,
                        chunk.heading_path,
                        chunk.acl_tags,
                        chunk.page,
                        Vector(list(embedding)),
                    )
                    for chunk, embedding in zip(chunks, embeddings, strict=True)
                ],
            )

    def search(
        self,
        embedding: Sequence[float],
        principals: Sequence[str],
        limit: int = 8,
        source_ids: Sequence[UUID] | None = None,
    ) -> list[RetrievedChunk]:
        params = {
            "embedding": Vector(list(embedding)),
            "principals": list(principals),
            "limit": limit,
        }
        if source_ids is None:
            sql = _SEARCH_SQL
        else:
            sql = _SEARCH_SQL_SCOPED
            params["source_ids"] = list(source_ids)
        rows = self._conn.execute(sql, params).fetchall()
        return [
            RetrievedChunk(
                id=row[0],
                document_id=row[1],
                ordinal=row[2],
                text=row[3],
                heading_path=row[4],
                acl_tags=row[5],
                page=row[6],
                source_uri=row[7],
                title=row[8],
                score=row[9],
            )
            for row in rows
        ]
