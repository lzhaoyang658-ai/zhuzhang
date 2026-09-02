from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Event, Thread

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.database import SessionLocal
from app.models import ProjectExportJob, QuoteParseJob, WorkerHeartbeat


settings = get_settings()


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo:
        return value
    return value.replace(tzinfo=timezone.utc)


def touch_worker(
    db,
    worker_id: str,
    queue_name: str,
    *,
    status: str,
    current_job_id: str | None = None,
    processed_delta: int = 0,
    failed_delta: int = 0,
) -> WorkerHeartbeat:
    now = datetime.now(timezone.utc)
    heartbeat = db.get(WorkerHeartbeat, worker_id)
    if not heartbeat:
        heartbeat = WorkerHeartbeat(
            worker_id=worker_id,
            queue_name=queue_name,
            status=status,
            processed_count=0,
            failed_count=0,
            started_at=now,
            last_seen_at=now,
        )
        db.add(heartbeat)
    heartbeat.queue_name = queue_name
    heartbeat.status = status
    heartbeat.current_job_id = current_job_id
    heartbeat.processed_count += processed_delta
    heartbeat.failed_count += failed_delta
    heartbeat.last_seen_at = now
    db.commit()
    return heartbeat


def touch_worker_with_new_session(
    worker_id: str,
    queue_name: str,
    *,
    status: str,
    current_job_id: str | None = None,
    processed_delta: int = 0,
    failed_delta: int = 0,
) -> None:
    db = SessionLocal()
    try:
        touch_worker(
            db,
            worker_id,
            queue_name,
            status=status,
            current_job_id=current_job_id,
            processed_delta=processed_delta,
            failed_delta=failed_delta,
        )
    finally:
        db.close()


@contextmanager
def worker_pulse(worker_id: str, queue_name: str, job_id: str):
    stopped = Event()

    def pulse() -> None:
        while not stopped.wait(max(1.0, settings.worker_heartbeat_seconds)):
            try:
                touch_worker_with_new_session(worker_id, queue_name, status="busy", current_job_id=job_id)
            except Exception:
                continue

    touch_worker_with_new_session(worker_id, queue_name, status="busy", current_job_id=job_id)
    thread = Thread(target=pulse, name=f"{queue_name}-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=2)


def finish_worker_job(worker_id: str, queue_name: str, *, failed: bool) -> None:
    touch_worker_with_new_session(
        worker_id,
        queue_name,
        status="idle",
        processed_delta=1,
        failed_delta=1 if failed else 0,
    )


def queue_health_snapshot(db, project_ids: set[str] | None = None) -> dict:
    now = datetime.now(timezone.utc)
    active_since = now - timedelta(seconds=settings.worker_heartbeat_stale_seconds)
    heartbeats = db.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc())).all()

    def task_rows(model):
        statement = select(model)
        if project_ids is not None:
            if not project_ids:
                return []
            statement = statement.where(model.project_id.in_(project_ids))
        return list(db.scalars(statement).all())

    def summarize(queue_name: str, mode: str, rows: list) -> dict:
        workers = [item for item in heartbeats if item.queue_name == queue_name and _aware(item.last_seen_at) >= active_since]
        queued_rows = [item for item in rows if item.status == "queued"]
        delayed = [item for item in queued_rows if _aware(item.next_attempt_at) and _aware(item.next_attempt_at) > now]
        oldest = min((_aware(item.created_at) for item in queued_rows), default=None)
        return {
            "mode": mode,
            "status": "healthy" if mode == "embedded" or workers else "degraded",
            "workers_alive": len(workers),
            "workers_busy": sum(1 for item in workers if item.status == "busy"),
            "queued": len(queued_rows),
            "retry_waiting": len(delayed),
            "running": sum(1 for item in rows if item.status == "running"),
            "dead_letter": sum(1 for item in rows if item.status == "dead_letter"),
            "oldest_queued_at": oldest,
        }

    return {
        "checked_at": now,
        "quote": summarize("quote", settings.quote_execution_mode, task_rows(QuoteParseJob)),
        "export": summarize("export", settings.export_execution_mode, task_rows(ProjectExportJob)),
    }


def prune_stale_worker_heartbeats(db, *, days: int = 7) -> int:
    threshold = datetime.now(timezone.utc) - timedelta(days=days)
    result = db.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.last_seen_at < threshold))
    db.commit()
    return result.rowcount or 0
