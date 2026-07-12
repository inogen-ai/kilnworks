import os
import signal
import threading
import time
from pathlib import Path

import psycopg
import pytest

from kilnworks.adapters.jobqueue import Job, PgJobQueue
from kilnworks.db.connection import connect, init_db
from kilnworks.settings import Settings
from kilnworks.worker.loop import (
    _cleanup_upload,
    _sweep_orphaned_uploads,
    _time_limit,
    run_worker,
)


def _settings(pg_url):
    return Settings(database_url=pg_url, fake_providers=True, openai_api_key="")


def test_worker_processes_upload_job_end_to_end(pg_url, tmp_path):
    conn = connect(pg_url)
    init_db(conn)
    (tmp_path / "upload.md").write_text("# Guide\n\nkilns are hot")
    queue = PgJobQueue(conn)
    job_id = queue.enqueue(
        "ingest_upload",
        {"path": str(tmp_path / "upload.md"), "acl_tags": ["public"], "title": "guide"},
        created_by="user-99",
    )
    processed = run_worker(_settings(pg_url), once=True)
    assert processed == 1
    assert queue.get(job_id).status == "done"
    row = conn.execute("SELECT title, status FROM documents").fetchone()
    assert row == ("guide", "ready")
    assert conn.execute(
        "SELECT DISTINCT user_id FROM cost_events WHERE context = 'ingest'"
    ).fetchall() == [("user-99",)]
    conn.execute("TRUNCATE documents CASCADE")
    conn.execute("TRUNCATE cost_events")
    conn.execute("TRUNCATE jobs")
    conn.close()


def test_worker_fails_job_for_bad_file_and_drains(pg_url, tmp_path):
    conn = connect(pg_url)
    init_db(conn)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-not really")
    queue = PgJobQueue(conn)
    job_id = queue.enqueue(
        "ingest_upload", {"path": str(bad), "acl_tags": ["public"], "title": "bad"}
    )
    processed = run_worker(_settings(pg_url), once=True)
    assert processed == 3                      # retried to max_attempts, drained
    final = queue.get(job_id)
    assert final.status == "failed" and final.error
    conn.execute("TRUNCATE documents CASCADE")
    conn.execute("TRUNCATE cost_events")
    conn.execute("TRUNCATE jobs")
    conn.close()


def test_worker_fails_unknown_job_kind(pg_url):
    conn = connect(pg_url)
    init_db(conn)
    queue = PgJobQueue(conn)
    job_id = queue.enqueue("mystery", {})
    run_worker(_settings(pg_url), once=True)
    final = queue.get(job_id)
    assert final.status == "failed" and "unknown job kind" in final.error
    conn.execute("TRUNCATE jobs")
    conn.close()


def test_time_limit_raises_and_cleans_up():
    with pytest.raises(TimeoutError):
        with _time_limit(0.05):
            time.sleep(0.5)
    time.sleep(0.1)  # were the timer still armed, it would fire here and kill the test


def test_worker_cleans_up_upload_file_on_success(pg_url, tmp_path):
    conn = connect(pg_url)
    init_db(conn)
    upload = tmp_path / "upload.md"
    upload.write_text("# Guide\n\nkilns are hot")
    queue = PgJobQueue(conn)
    job_id = queue.enqueue(
        "ingest_upload",
        {"path": str(upload), "acl_tags": ["public"], "title": "guide"},
    )
    processed = run_worker(_settings(pg_url), once=True)
    assert processed == 1
    assert queue.get(job_id).status == "done"
    assert not upload.exists()
    conn.execute("TRUNCATE documents CASCADE")
    conn.execute("TRUNCATE cost_events")
    conn.execute("TRUNCATE jobs")
    conn.close()


def test_worker_cleans_up_upload_file_on_terminal_failure(pg_url, tmp_path):
    conn = connect(pg_url)
    init_db(conn)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-not really")
    queue = PgJobQueue(conn)
    job_id = queue.enqueue(
        "ingest_upload", {"path": str(bad), "acl_tags": ["public"], "title": "bad"}
    )
    conn.execute("UPDATE jobs SET max_attempts = 1 WHERE id = %s", (job_id,))
    processed = run_worker(_settings(pg_url), once=True)
    assert processed == 1
    final = queue.get(job_id)
    assert final.status == "failed" and final.error
    assert not bad.exists()
    conn.execute("TRUNCATE jobs")
    conn.close()


def test_worker_keeps_upload_file_on_retryable_failure(pg_url, tmp_path, monkeypatch):
    conn = connect(pg_url)
    init_db(conn)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-not really")
    queue = PgJobQueue(conn)
    job_id = queue.enqueue(
        "ingest_upload", {"path": str(bad), "acl_tags": ["public"], "title": "bad"}
    )
    # max_attempts is 3 by default: limit the worker to a single claim so we can
    # observe the state right after the first (retryable) failure.
    real_claim = PgJobQueue.claim
    calls = {"n": 0}

    def claim_once(self):
        calls["n"] += 1
        if calls["n"] > 1:
            return None
        return real_claim(self)

    monkeypatch.setattr(PgJobQueue, "claim", claim_once)
    processed = run_worker(_settings(pg_url), once=True)
    assert processed == 1
    job = queue.get(job_id)
    assert job.status == "queued" and job.attempts == 1
    assert bad.exists()
    conn.execute("TRUNCATE jobs")
    conn.close()


def test_cleanup_upload_never_raises_on_unlink_error(monkeypatch, capsys):
    job = Job(
        id=1,
        kind="ingest_upload",
        payload={"path": "/tmp/undeletable.md"},
        status="done",
        attempts=1,
        max_attempts=3,
    )

    def _raise_permission_error(self, missing_ok=False):
        raise PermissionError("permission denied")

    monkeypatch.setattr(Path, "unlink", _raise_permission_error)

    _cleanup_upload(job)  # must not raise: best-effort cleanup can't crash the worker

    assert "worker:" in capsys.readouterr().err


def test_run_worker_rejects_lease_not_exceeding_timeout(monkeypatch):
    def fail_if_called(url):
        raise AssertionError("run_worker must validate settings before connecting")

    monkeypatch.setattr("kilnworks.worker.loop.connect", fail_if_called)
    settings = Settings(
        # Unroutable host: connect() would hang/fail rather than raise ValueError if
        # the lease/timeout check didn't happen first, proving no connection is attempted.
        database_url="postgresql://kw@10.255.255.1:5432/kw",
        fake_providers=True,
        openai_api_key="",
        job_lease_seconds=300,
        job_timeout_seconds=300,
    )

    with pytest.raises(ValueError, match="KILNWORKS_JOB_LEASE_SECONDS.*must exceed"):
        run_worker(settings, once=True)


def test_run_worker_once_propagates_operational_error(pg_url, monkeypatch):
    def always_raise(url):
        raise psycopg.OperationalError("simulated outage")

    monkeypatch.setattr("kilnworks.worker.loop.connect", always_raise)
    with pytest.raises(psycopg.OperationalError):
        run_worker(_settings(pg_url), once=True)


def test_run_worker_reconnects_with_growing_backoff_then_recovers(pg_url, monkeypatch):
    conn = connect(pg_url)
    init_db(conn)
    conn.close()

    real_connect = connect
    attempts = {"n": 0}

    def flaky_connect(url):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise psycopg.OperationalError("simulated outage")
        return real_connect(url)

    monkeypatch.setattr("kilnworks.worker.loop.connect", flaky_connect)

    waits = []

    def fake_wait(self, timeout=None):
        waits.append(timeout)
        return self.is_set()

    monkeypatch.setattr(threading.Event, "wait", fake_wait)

    def claim_and_stop(self):
        # Successful reconnect: end the daemon loop by delivering SIGINT, which
        # run_worker's own handler (installed because once=False) turns into a
        # clean stop — the same mechanism an operator would use.
        os.kill(os.getpid(), signal.SIGINT)
        return None

    monkeypatch.setattr(PgJobQueue, "claim", claim_and_stop)

    processed = run_worker(_settings(pg_url), once=False)

    assert processed == 0
    assert attempts["n"] == 3
    assert waits[:2] == [1.0, 2.0]


def test_sigterm_interrupts_poll_sleep_promptly(pg_url):
    """Regression for the PEP 475 pitfall: a plain time.sleep(worker_poll_seconds)
    auto-retries across a delivered signal and waits out the full interval before the
    loop condition is re-checked — well past docker's stop grace period. Swapping to
    threading.Event.wait() means the wait returns as soon as the signal handler sets
    the event, so shutdown stays prompt even with a long poll interval."""
    conn = connect(pg_url)
    init_db(conn)
    conn.close()
    settings = Settings(
        database_url=pg_url, fake_providers=True, openai_api_key="", worker_poll_seconds=30.0
    )

    def _deliver_sigterm():
        time.sleep(0.2)
        os.kill(os.getpid(), signal.SIGTERM)

    thread = threading.Thread(target=_deliver_sigterm)
    started = time.monotonic()
    thread.start()
    processed = run_worker(settings, once=False)
    elapsed = time.monotonic() - started
    thread.join()

    assert processed == 0
    assert elapsed < 5.0  # would be ~30s with a plain time.sleep


# --- orphaned uploads sweep ----------------------------------------------------


def test_sweep_orphaned_uploads_deletes_old_unreferenced_file(pg_url, tmp_path):
    conn = connect(pg_url)
    init_db(conn)
    queue = PgJobQueue(conn)
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    orphan = uploads_dir / "orphan.md"
    orphan.write_text("stale")
    old = time.time() - 1000
    os.utime(orphan, (old, old))

    _sweep_orphaned_uploads(queue, str(tmp_path), lease_seconds=60)

    assert not orphan.exists()
    conn.close()


def test_sweep_orphaned_uploads_keeps_file_referenced_by_queued_job(pg_url, tmp_path):
    conn = connect(pg_url)
    init_db(conn)
    queue = PgJobQueue(conn)
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    referenced = uploads_dir / "referenced.md"
    referenced.write_text("keep me")
    old = time.time() - 1000
    os.utime(referenced, (old, old))
    queue.enqueue(
        "ingest_upload", {"path": str(referenced.resolve()), "acl_tags": ["public"]}
    )

    _sweep_orphaned_uploads(queue, str(tmp_path), lease_seconds=60)

    assert referenced.exists()
    conn.execute("TRUNCATE jobs")
    conn.close()


def test_sweep_orphaned_uploads_keeps_young_file(pg_url, tmp_path):
    conn = connect(pg_url)
    init_db(conn)
    queue = PgJobQueue(conn)
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    fresh = uploads_dir / "fresh.md"
    fresh.write_text("new")

    _sweep_orphaned_uploads(queue, str(tmp_path), lease_seconds=60)

    assert fresh.exists()
    conn.close()
