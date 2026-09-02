from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from math import ceil
from pathlib import Path
from socket import gethostname
from threading import Lock, Timer
from uuid import uuid4

from sqlalchemy import delete, or_, select, update

from app.core.config import get_settings
from app.database import SessionLocal
from app.models import Project, Quote, QuoteItem, QuoteParseJob
from app.services.audit import record_event
from app.services.notifications import create_event_notifications
from app.services.quote_parser import parse_quote_document
from app.services.worker_health import finish_worker_job, touch_worker_with_new_session, worker_pulse
from app.services.source_storage import get_source_storage


settings = get_settings()
executor: ThreadPoolExecutor | None = None
scheduled_job_ids: set[str] = set()
retry_timers: dict[str, Timer] = {}
scheduled_lock = Lock()


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo:
        return value
    return value.replace(tzinfo=timezone.utc)


def _worker_id(prefix: str = "quote-worker") -> str:
    return f"{prefix}:{gethostname()}:{uuid4().hex[:10]}"


def quote_job_payload(job: QuoteParseJob | None) -> dict | None:
    if not job:
        return None
    return {
        "id": job.id,
        "quote_id": job.quote_id,
        "status": "failed" if job.status == "dead_letter" else job.status,
        "progress": job.progress,
        "stage": job.stage,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "retry_scheduled_at": job.next_attempt_at,
        "error_message": job.error_message,
        "parse_method": job.parse_method,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "updated_at": job.updated_at,
    }


def _set_job(job: QuoteParseJob, *, progress: int, stage: str) -> None:
    job.progress = max(0, min(100, progress))
    job.stage = stage
    job.updated_at = datetime.now(timezone.utc)


def _claim_specific_job(db, job_id: str, worker_id: str) -> QuoteParseJob | None:
    now = datetime.now(timezone.utc)
    job = db.get(QuoteParseJob, job_id)
    if not job or job.status != "queued" or (_aware(job.next_attempt_at) and _aware(job.next_attempt_at) > now):
        return None
    result = db.execute(
        update(QuoteParseJob)
        .where(QuoteParseJob.id == job_id, QuoteParseJob.status == "queued")
        .values(
            status="running",
            attempt=QuoteParseJob.attempt + 1,
            lease_owner=worker_id,
            lease_expires_at=now + timedelta(seconds=settings.quote_job_lease_seconds),
            next_attempt_at=None,
            started_at=now,
            updated_at=now,
        )
    )
    db.commit()
    return db.get(QuoteParseJob, job_id) if result.rowcount == 1 else None


def claim_next_quote_job(db, worker_id: str) -> QuoteParseJob | None:
    now = datetime.now(timezone.utc)
    exhausted = db.scalars(
        select(QuoteParseJob).where(
            QuoteParseJob.status == "running",
            QuoteParseJob.lease_expires_at <= now,
            QuoteParseJob.attempt >= QuoteParseJob.max_attempts,
        )
    ).all()
    for job in exhausted:
        job.status = "dead_letter"
        job.stage = "重试次数已用尽"
        job.finished_at = now
        job.lease_owner = None
        job.lease_expires_at = None
        quote = db.get(Quote, job.quote_id)
        if quote:
            quote.status = "parse_failed"
    db.commit()

    candidate = db.scalar(
        select(QuoteParseJob)
        .where(
            QuoteParseJob.attempt < QuoteParseJob.max_attempts,
            or_(
                (QuoteParseJob.status == "queued") & or_(QuoteParseJob.next_attempt_at.is_(None), QuoteParseJob.next_attempt_at <= now),
                (QuoteParseJob.status == "running") & (QuoteParseJob.lease_expires_at <= now),
            ),
        )
        .order_by(QuoteParseJob.created_at)
        .limit(1)
    )
    if not candidate:
        return None
    original_status = candidate.status
    conditions = [QuoteParseJob.id == candidate.id, QuoteParseJob.status == original_status]
    if original_status == "running":
        conditions.append(QuoteParseJob.lease_expires_at <= now)
    result = db.execute(
        update(QuoteParseJob)
        .where(*conditions)
        .values(
            status="running",
            attempt=QuoteParseJob.attempt + 1,
            lease_owner=worker_id,
            lease_expires_at=now + timedelta(seconds=settings.quote_job_lease_seconds),
            next_attempt_at=None,
            started_at=now,
            updated_at=now,
        )
    )
    db.commit()
    return db.get(QuoteParseJob, candidate.id) if result.rowcount == 1 else None


def process_quote_job_in_session(db, job_id: str, *, worker_id: str | None = None, claimed: bool = False) -> None:
    worker_id = worker_id or _worker_id("inline-quote")
    try:
        job = db.get(QuoteParseJob, job_id) if claimed else _claim_specific_job(db, job_id, worker_id)
        if not job or job.status != "running" or job.lease_owner != worker_id:
            return
        quote = db.get(Quote, job.quote_id)
        if not quote:
            job.status = "dead_letter"
            job.error_message = "关联报价不存在"
            job.finished_at = datetime.now(timezone.utc)
            job.lease_owner = None
            job.lease_expires_at = None
            _set_job(job, progress=100, stage="解析失败")
            db.commit()
            return

        job.finished_at = None
        job.error_message = None
        quote.status = "parsing"
        quote.error_message = None
        _set_job(job, progress=15, stage="读取原始文件")
        db.commit()

        source_path = get_source_storage().ensure_local(f"{quote.project_id}/quotes/{quote.object_key}")
        if not source_path:
            raise ValueError("报价原文件不存在")
        content = source_path.read_bytes()
        suffix = Path(quote.original_name).suffix.lower()
        _set_job(job, progress=35, stage="识别结构与金额")
        job.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.quote_job_lease_seconds)
        db.commit()

        result = parse_quote_document(content, suffix)
        _set_job(job, progress=85, stage="校验金额并保存草稿")
        job.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.quote_job_lease_seconds)
        db.commit()

        db.execute(delete(QuoteItem).where(QuoteItem.quote_id == quote.id))
        quote.status = "reviewing"
        quote.total_cents = sum(row["total_cents"] for row in result.items)
        quote.source_total_cents = result.source_total_cents
        quote.input_type = result.input_type
        quote.parse_method = result.parse_method
        quote.parser_version = result.parser_version
        quote.page_count = result.page_count
        quote.warnings = result.warnings
        quote.error_message = None
        db.add_all([QuoteItem(project_id=quote.project_id, quote_id=quote.id, **row) for row in result.items])

        job.status = "succeeded"
        job.parse_method = result.parse_method
        job.finished_at = datetime.now(timezone.utc)
        job.lease_owner = None
        job.lease_expires_at = None
        job.next_attempt_at = None
        _set_job(job, progress=100, stage="等待人工校对")
        record_event(
            db,
            project_id=quote.project_id,
            event_type="quote_uploaded",
            object_type="quote",
            object_id=quote.id,
            title=f"导入候选报价：{quote.name}",
            detail=f"提取 {len(result.items)} 个条目，等待人工校对；{result.parse_method}",
            actor="系统任务",
        )
        project = db.get(Project, quote.project_id)
        if project:
            create_event_notifications(
                db,
                project,
                code="QUOTE_PARSE_SUCCEEDED",
                level="info",
                title=f"报价解析完成：{quote.name}",
                message=f"已提取 {len(result.items)} 个条目，请进入预算页人工校对。",
                object_type="quote",
                object_id=quote.id,
                action_path=f"/?project={quote.project_id}&tab=budget",
            )
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.get(QuoteParseJob, job_id)
        if job:
            quote = db.get(Quote, job.quote_id)
            message = str(exc)[:1000]
            terminal = job.attempt >= job.max_attempts
            delay = settings.quote_retry_base_seconds * (2 ** max(0, job.attempt - 1))
            job.status = "dead_letter" if terminal else "queued"
            job.error_message = message
            job.finished_at = datetime.now(timezone.utc) if terminal else None
            job.lease_owner = None
            job.lease_expires_at = None
            job.next_attempt_at = None if terminal else datetime.now(timezone.utc) + timedelta(seconds=delay)
            _set_job(job, progress=100 if terminal else 5, stage="解析失败，需人工重试" if terminal else f"第 {job.attempt} 次失败，等待自动重试")
            if quote:
                quote.status = "parse_failed" if terminal else "queued"
                quote.error_message = message
                record_event(
                    db,
                    project_id=quote.project_id,
                    event_type="quote_parse_failed" if terminal else "quote_parse_retry_scheduled",
                    object_type="quote",
                    object_id=quote.id,
                    title=f"报价解析失败：{quote.name}" if terminal else f"报价等待自动重试：{quote.name}",
                    detail=message,
                    actor="系统任务",
                )
                project = db.get(Project, quote.project_id)
                if terminal and project:
                    create_event_notifications(
                        db,
                        project,
                        code="QUOTE_PARSE_FAILED",
                        level="warning",
                        title=f"报价解析失败：{quote.name}",
                        message=message,
                        object_type="quote",
                        object_id=quote.id,
                        action_path=f"/?project={quote.project_id}&tab=budget",
                        event_state="failed",
                    )
            db.commit()
            if not terminal and settings.quote_execution_mode == "embedded":
                _schedule_embedded_retry(job.id, delay)


def process_quote_job(job_id: str, *, worker_id: str | None = None, claimed: bool = False) -> None:
    db = SessionLocal()
    try:
        process_quote_job_in_session(db, job_id, worker_id=worker_id, claimed=claimed)
    finally:
        db.close()
        with scheduled_lock:
            scheduled_job_ids.discard(job_id)


def _schedule_embedded_retry(job_id: str, delay: int) -> None:
    def submit() -> None:
        with scheduled_lock:
            retry_timers.pop(job_id, None)
        submit_quote_job(job_id)

    timer = Timer(max(1, delay + 0.25), submit)
    timer.daemon = True
    with scheduled_lock:
        previous = retry_timers.pop(job_id, None)
        if previous:
            previous.cancel()
        retry_timers[job_id] = timer
    timer.start()


def submit_quote_job(job_id: str) -> bool:
    global executor
    if settings.quote_execution_mode == "worker":
        return True
    with scheduled_lock:
        if job_id in scheduled_job_ids:
            return False
        if executor is None:
            executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="quote-parser")
        scheduled_job_ids.add(job_id)
        try:
            executor.submit(process_quote_job, job_id)
        except RuntimeError:
            scheduled_job_ids.discard(job_id)
            raise
    return True


def run_quote_worker_once(worker_id: str) -> bool:
    db = SessionLocal()
    job_id: str | None = None
    try:
        job = claim_next_quote_job(db, worker_id)
        if job:
            job_id = job.id
    finally:
        db.close()
    if not job_id:
        touch_worker_with_new_session(worker_id, "quote", status="idle")
        return False
    with worker_pulse(worker_id, "quote", job_id):
        process_quote_job(job_id, worker_id=worker_id, claimed=True)
    db = SessionLocal()
    try:
        result = db.get(QuoteParseJob, job_id)
        failed = bool(result and result.status == "dead_letter")
    finally:
        db.close()
    finish_worker_job(worker_id, "quote", failed=failed)
    return True


def resume_incomplete_jobs() -> int:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        jobs = db.scalars(select(QuoteParseJob).where(QuoteParseJob.status.in_({"queued", "running"}))).all()
        resumable: list[tuple[QuoteParseJob, int]] = []
        for job in jobs:
            lease_expired = not _aware(job.lease_expires_at) or _aware(job.lease_expires_at) <= now
            if job.status == "running" and settings.quote_execution_mode == "worker" and not lease_expired:
                continue
            retry_at = _aware(job.next_attempt_at) if job.status == "queued" else None
            job.status = "queued"
            job.lease_owner = None
            job.lease_expires_at = None
            delay = max(1, ceil((retry_at - now).total_seconds())) if retry_at and retry_at > now else 0
            if delay:
                _set_job(job, progress=5, stage="服务恢复，等待自动重试")
            else:
                job.next_attempt_at = None
                _set_job(job, progress=5, stage="服务恢复，重新排队")
            resumable.append((job, delay))
        db.commit()
        for job, delay in resumable:
            if delay and settings.quote_execution_mode == "embedded":
                _schedule_embedded_retry(job.id, delay)
            else:
                submit_quote_job(job.id)
        return len(resumable)
    finally:
        db.close()


def shutdown_quote_jobs() -> None:
    global executor
    with scheduled_lock:
        current = executor
        executor = None
        timers = list(retry_timers.values())
        retry_timers.clear()
    for timer in timers:
        timer.cancel()
    if current is not None:
        current.shutdown(wait=False, cancel_futures=False)
