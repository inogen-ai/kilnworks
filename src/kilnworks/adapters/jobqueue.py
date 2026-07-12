import psycopg
from psycopg.types.json import Json
from pydantic import BaseModel

_CLAIM_SQL = """
UPDATE jobs
SET status = 'running', started_at = now(), attempts = attempts + 1
WHERE id = (
    SELECT id FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED
)
RETURNING id, kind, payload, status, attempts, max_attempts, error, created_by
"""

_GET_SQL = """
SELECT id, kind, payload, status, attempts, max_attempts, error, created_by
FROM jobs WHERE id = %s
"""


class Job(BaseModel):
    id: int
    kind: str
    payload: dict
    status: str
    attempts: int
    max_attempts: int
    error: str | None = None
    created_by: str | None = None


def _row_to_job(row: tuple) -> Job:
    return Job(
        id=row[0],
        kind=row[1],
        payload=row[2],
        status=row[3],
        attempts=row[4],
        max_attempts=row[5],
        error=row[6],
        created_by=row[7],
    )


class PgJobQueue:
    """Postgres-backed job queue using FOR UPDATE SKIP LOCKED claims."""

    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    def enqueue(self, kind: str, payload: dict, created_by: str | None = None) -> int:
        row = self._conn.execute(
            "INSERT INTO jobs (kind, payload, created_by) VALUES (%s, %s, %s) RETURNING id",
            (kind, Json(payload), created_by),
        ).fetchone()
        return row[0]

    def claim(self) -> Job | None:
        row = self._conn.execute(_CLAIM_SQL).fetchone()
        return _row_to_job(row) if row else None

    def complete(self, job: Job) -> None:
        """Fenced on `attempts`: every claim increments it, so it uniquely identifies
        this execution. A stale caller (e.g. a worker that stalled past its lease and
        was reclaimed, then re-claimed by another worker) can't mark the new execution
        done — its attempts value no longer matches."""
        self._conn.execute(
            "UPDATE jobs SET status = 'done', finished_at = now(), error = NULL "
            "WHERE id = %s AND status = 'running' AND attempts = %s",
            (job.id, job.attempts),
        )

    def fail(self, job: Job, error: str) -> None:
        """Fenced on `attempts`; see `complete` for why."""
        if job.attempts < job.max_attempts:
            self._conn.execute(
                "UPDATE jobs SET status = 'queued', error = %s "
                "WHERE id = %s AND status = 'running' AND attempts = %s",
                (error, job.id, job.attempts),
            )
        else:
            self._conn.execute(
                """UPDATE jobs SET status = 'failed', error = %s, finished_at = now()
                   WHERE id = %s AND status = 'running' AND attempts = %s""",
                (error, job.id, job.attempts),
            )

    def get(self, job_id: int) -> Job | None:
        row = self._conn.execute(_GET_SQL, (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def referenced_upload_paths(self) -> set[str]:
        """Payload `path` values for every queued/running job. Used by the worker's
        orphaned-uploads sweep to avoid deleting a file a not-yet-processed job still
        needs."""
        rows = self._conn.execute(
            "SELECT payload->>'path' FROM jobs WHERE status IN ('queued', 'running')"
        ).fetchall()
        return {row[0] for row in rows if row[0]}

    def reclaim_expired(self, lease_seconds: float) -> list[Job]:
        """Recover jobs stranded in 'running' by a dead worker. A running job whose
        started_at is older than the lease is presumed abandoned: live jobs finish or
        time out (SIGALRM) well inside the lease, so this is safe with multiple
        workers. Expired jobs with attempts remaining are requeued; exhausted ones are
        terminally failed and returned so the caller can clean up their artifacts."""
        failed_rows = self._conn.execute(
            """UPDATE jobs SET status = 'failed',
               error = 'lease expired (worker died; attempts exhausted)',
               finished_at = now()
               WHERE status = 'running' AND attempts >= max_attempts
                 AND started_at < now() - make_interval(secs => %s)
               RETURNING id, kind, payload, status, attempts, max_attempts, error, created_by""",
            (lease_seconds,),
        ).fetchall()
        self._conn.execute(
            """UPDATE jobs SET status = 'queued'
               WHERE status = 'running' AND attempts < max_attempts
                 AND started_at < now() - make_interval(secs => %s)""",
            (lease_seconds,),
        )
        return [_row_to_job(row) for row in failed_rows]
