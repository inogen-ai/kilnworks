import signal
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import psycopg

from kilnworks.adapters.jobqueue import Job, PgJobQueue
from kilnworks.adapters.sources.singlefile import SingleFileSource
from kilnworks.db.connection import connect
from kilnworks.settings import Settings
from kilnworks.wiring import Services, build_services_with_conn

_RECLAIM_INTERVAL_SECONDS = 60.0
_MAX_BACKOFF_SECONDS = 30.0


@contextmanager
def _time_limit(seconds: float):
    """SIGALRM-based job timeout. Main-thread + Unix only (the worker's context)."""

    def _raise(signum, frame):
        raise TimeoutError(f"job exceeded {seconds}s")

    previous = signal.signal(signal.SIGALRM, _raise)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _process(job: Job, services: Services, queue: PgJobQueue, timeout_seconds: float) -> None:
    if job.kind != "ingest_upload":
        queue.fail(job, f"unknown job kind: {job.kind}")
        return
    source = SingleFileSource(
        Path(job.payload["path"]),
        acl_tags=job.payload.get("acl_tags", ["public"]),
        title=job.payload.get("title"),
    )
    with _time_limit(timeout_seconds):
        report = services.ingestion.ingest(source, user_id=job.created_by)
    if report.failed:
        queue.fail(job, report.failed[0][1])
    else:
        queue.complete(job)


def _cleanup_upload(job: Job) -> None:
    """Uploaded files are consumed at ingestion; once a job is terminal the copy
    under data/uploads is orphaned. Best-effort delete: missing_ok=True still
    raises on e.g. a permission error or a directory, so any OSError is swallowed
    here rather than left to crash the worker loop."""
    path = job.payload.get("path")
    if job.kind == "ingest_upload" and path:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:
            print(f"worker: couldn't remove upload {path!r}: {exc}", file=sys.stderr)


def _sweep_orphaned_uploads(queue: PgJobQueue, data_dir: str, lease_seconds: float) -> None:
    """Catches uploads that `_cleanup_upload` never got a chance to remove — e.g. a
    file written by /documents whose enqueue never committed, or a job whose
    terminal-state cleanup was missed across a worker restart. A file under
    <data_dir>/uploads is a deletion candidate once it's older than the job lease
    (any live job would have finished or been reclaimed well inside that window) and
    isn't referenced by a still-queued/running job's payload. Best-effort per file:
    an OSError removing one file is logged and skipped rather than aborting the sweep."""
    uploads_dir = Path(data_dir).resolve() / "uploads"
    if not uploads_dir.is_dir():
        return
    referenced = queue.referenced_upload_paths()
    cutoff = time.time() - lease_seconds
    try:
        entries = list(uploads_dir.iterdir())
    except OSError as exc:  # dir vanished/unreadable mid-sweep: skip, never crash the daemon
        print(f"worker: couldn't scan uploads dir {uploads_dir}: {exc}", file=sys.stderr)
        return
    for entry in entries:
        try:
            if not entry.is_file():
                continue
            if str(entry) in referenced:
                continue
            if entry.stat().st_mtime >= cutoff:
                continue
            entry.unlink()
        except OSError as exc:
            print(f"worker: couldn't remove orphaned upload {entry!r}: {exc}", file=sys.stderr)


def run_worker(settings: Settings, once: bool = False) -> int:
    if settings.job_lease_seconds <= settings.job_timeout_seconds:
        raise ValueError(
            f"KILNWORKS_JOB_LEASE_SECONDS ({settings.job_lease_seconds}) must exceed "
            f"KILNWORKS_JOB_TIMEOUT_SECONDS ({settings.job_timeout_seconds})"
        )
    stop_event = threading.Event()
    if not once:
        def _stop(signum, frame):
            stop_event.set()

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)

    processed = 0
    backoff = 1.0
    conn = None
    last_reclaim = 0.0
    try:
        while not stop_event.is_set():
            try:
                if conn is None or conn.closed:
                    conn = connect(settings.database_url)
                    services = build_services_with_conn(settings, conn)
                    queue = PgJobQueue(conn)
                    backoff = 1.0
                    last_reclaim = 0.0
                if time.monotonic() - last_reclaim >= _RECLAIM_INTERVAL_SECONDS:
                    for dead in queue.reclaim_expired(settings.job_lease_seconds):
                        _cleanup_upload(dead)
                    _sweep_orphaned_uploads(queue, settings.data_dir, settings.job_lease_seconds)
                    last_reclaim = time.monotonic()
                job = queue.claim()
                if job is None:
                    if once:
                        return processed
                    stop_event.wait(settings.worker_poll_seconds)
                    continue
                try:
                    _process(job, services, queue, settings.job_timeout_seconds)
                except psycopg.OperationalError:
                    raise  # connection loss: handled by the outer reconnect path
                except Exception as exc:  # timeout or unexpected: fail the job, keep going
                    queue.fail(job, str(exc))
                refreshed = queue.get(job.id)
                if refreshed is not None and refreshed.status in ("done", "failed"):
                    _cleanup_upload(refreshed)
                processed += 1
            except psycopg.OperationalError as exc:
                if once:
                    raise
                print(
                    f"worker: database unavailable ({exc}); retrying in {backoff:.0f}s",
                    file=sys.stderr,
                )
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                conn = None
                stop_event.wait(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
        return processed
    finally:
        if conn is not None:
            conn.close()
