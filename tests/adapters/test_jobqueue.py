from kilnworks.adapters.jobqueue import PgJobQueue
from kilnworks.db.connection import connect


def test_enqueue_claim_complete_roundtrip(conn):
    queue = PgJobQueue(conn)
    job_id = queue.enqueue("ingest_upload", {"path": "/tmp/x.md", "acl_tags": ["public"]})
    job = queue.claim()
    assert job.id == job_id and job.kind == "ingest_upload"
    assert job.payload["acl_tags"] == ["public"]
    assert job.status == "running" and job.attempts == 1
    queue.complete(job)
    assert queue.get(job.id).status == "done"


def test_claimed_job_is_invisible_to_second_claim(conn, pg_url):
    queue = PgJobQueue(conn)
    queue.enqueue("ingest_upload", {})
    first = queue.claim()
    assert first is not None
    other_conn = connect(pg_url)
    try:
        assert PgJobQueue(other_conn).claim() is None
    finally:
        other_conn.close()


def test_fail_requeues_until_max_attempts_then_fails(conn):
    queue = PgJobQueue(conn)
    queue.enqueue("ingest_upload", {})
    for attempt in (1, 2, 3):
        job = queue.claim()
        assert job.attempts == attempt
        queue.fail(job, f"boom {attempt}")
    assert queue.claim() is None
    final = queue.get(job.id)
    assert final.status == "failed" and "boom 3" in final.error


def test_claim_returns_none_on_empty_queue(conn):
    assert PgJobQueue(conn).claim() is None


def test_complete_and_fail_only_touch_running_jobs(conn):
    queue = PgJobQueue(conn)
    queue.enqueue("ingest_upload", {})
    job = queue.claim()
    queue.complete(job)
    queue.fail(job, "late timeout")          # must be a no-op on a done job
    assert queue.get(job.id).status == "done"


def test_reclaim_requeues_expired_running_job(conn):
    queue = PgJobQueue(conn)
    job_id = queue.enqueue("ingest_upload", {"path": "/tmp/x.md"})
    queue.claim()
    conn.execute(
        "UPDATE jobs SET started_at = now() - interval '10 minutes' WHERE id = %s", (job_id,)
    )
    failed = queue.reclaim_expired(lease_seconds=420)
    assert failed == []
    assert queue.get(job_id).status == "queued"


def test_reclaim_leaves_fresh_running_job(conn):
    queue = PgJobQueue(conn)
    job_id = queue.enqueue("ingest_upload", {"path": "/tmp/x.md"})
    queue.claim()  # started_at = now()
    queue.reclaim_expired(lease_seconds=420)
    assert queue.get(job_id).status == "running"


def test_complete_and_fail_are_fenced_against_a_stale_claim(conn):
    """A worker that stalls past its lease can have its execution reclaimed and
    re-claimed by another worker. The stalled worker's late complete()/fail() must
    not land on the new execution — it's still holding the Job object from its own
    (now stale) claim, whose `attempts` no longer matches."""
    queue = PgJobQueue(conn)
    job_id = queue.enqueue("ingest_upload", {"path": "/tmp/x.md"})
    stale = queue.claim()  # attempts == 1
    assert stale.attempts == 1
    conn.execute(
        "UPDATE jobs SET started_at = now() - interval '10 minutes' WHERE id = %s", (job_id,)
    )
    assert queue.reclaim_expired(lease_seconds=420) == []
    assert queue.get(job_id).status == "queued"

    current = queue.claim()  # attempts == 2: a new execution
    assert current.attempts == 2

    # The stale worker's late calls, using the first (stale) Job object, must be no-ops.
    queue.complete(stale)
    assert queue.get(job_id).status == "running" and queue.get(job_id).attempts == 2
    queue.fail(stale, "stale failure")
    assert queue.get(job_id).status == "running" and queue.get(job_id).attempts == 2

    # The current holder's calls, using the fresh Job object, still work.
    queue.complete(current)
    assert queue.get(job_id).status == "done"


def test_complete_clears_error_left_by_earlier_failed_attempt(conn):
    """A job that fails once (leaving `error` set) and then succeeds on retry
    must not carry the stale error text forward once it's done."""
    queue = PgJobQueue(conn)
    job_id = queue.enqueue("ingest_upload", {"path": "/tmp/x.md"})
    first = queue.claim()
    queue.fail(first, "transient boom")
    assert queue.get(job_id).error == "transient boom"

    second = queue.claim()
    queue.complete(second)

    final = queue.get(job_id)
    assert final.status == "done"
    assert final.error is None


def test_reclaim_fails_expired_job_with_exhausted_attempts(conn):
    queue = PgJobQueue(conn)
    job_id = queue.enqueue("ingest_upload", {"path": "/tmp/x.md"})
    conn.execute("UPDATE jobs SET max_attempts = 1 WHERE id = %s", (job_id,))
    queue.claim()
    conn.execute(
        "UPDATE jobs SET started_at = now() - interval '10 minutes' WHERE id = %s", (job_id,)
    )
    failed = queue.reclaim_expired(lease_seconds=420)
    assert [j.id for j in failed] == [job_id]
    job = queue.get(job_id)
    assert job.status == "failed"
    assert "lease expired" in job.error
