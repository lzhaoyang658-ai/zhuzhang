from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from math import ceil
from pathlib import Path
from socket import gethostname
from threading import Lock, Timer
from uuid import uuid4

from sqlalchemy import or_, select, update

from app.core.config import get_settings
from app.database import SessionLocal
from app.models import Project, ProjectExportArtifact, ProjectExportJob
from app.services.audit import record_event
from app.services.artifact_storage import get_artifact_storage
from app.services.exporter import REPORT_VERSION, create_project_archive
from app.services.notifications import create_event_notifications
from app.services.worker_health import finish_worker_job, touch_worker_with_new_session, worker_pulse


settings = get_settings()
executor: ThreadPoolExecutor | None = None
scheduled_job_ids: set[str] = set()
retry_timers: dict[str, Timer] = {}
scheduled_lock = Lock()


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo:
        return value
    return value.replace(tzinfo=timezone.utc)


def _worker_id(prefix: str = "worker") -> str:
    return f"{prefix}:{gethostname()}:{uuid4().hex[:10]}"


def artifact_payload(artifact: ProjectExportArtifact) -> dict:
    return {
        "id": artifact.id,
        "kind": artifact.kind,
        "part_number": artifact.part_number,
        "filename": artifact.filename,
        "size_bytes": artifact.size_bytes,
        "integrity_protected": bool(artifact.sha256),
        "storage_backend": artifact.storage_backend,
        "download_path": f"/project-export-jobs/{artifact.job_id}/artifacts/{artifact.id}/download",
    }


def export_job_payload(job: ProjectExportJob | None) -> dict | None:
    if not job:
        return None
    expires_at = _aware(job.expires_at)
    downloadable = job.status == "succeeded" and bool(expires_at and expires_at > datetime.now(timezone.utc))
    artifacts = sorted(job.artifacts, key=lambda item: item.part_number)
    artifact_items = [artifact_payload(item) for item in artifacts]
    if downloadable and not artifact_items and job.object_key:
        artifact_items = [{
            "id": "legacy",
            "kind": "primary",
            "part_number": 1,
            "filename": "项目档案-主卷.zip",
            "size_bytes": job.file_size_bytes,
            "integrity_protected": bool(job.artifact_sha256),
            "storage_backend": job.storage_backend,
            "download_path": f"/project-export-jobs/{job.id}/download",
        }]
    return {
        "id": job.id,
        "project_id": job.project_id,
        "status": job.status,
        "progress": job.progress,
        "stage": job.stage,
        "include_attachments": job.include_attachments,
        "date_from": job.date_from,
        "date_to": job.date_to,
        "file_size_bytes": job.file_size_bytes,
        "integrity_protected": all(item.sha256 for item in artifacts) if artifacts else bool(job.artifact_sha256),
        "storage_backend": job.storage_backend,
        "report_version": job.report_version,
        "report_page_count": job.report_page_count,
        "part_count": job.part_count or len(artifact_items),
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "artifacts": artifact_items if downloadable else [],
        "error_message": job.error_message,
        "expires_at": job.expires_at,
        "downloadable": downloadable,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "updated_at": job.updated_at,
    }


def _set_job(job: ProjectExportJob, *, progress: int, stage: str) -> None:
    job.progress = max(0, min(100, progress))
    job.stage = stage
    job.updated_at = datetime.now(timezone.utc)


def export_artifact_path(item: ProjectExportArtifact | ProjectExportJob) -> Path | None:
    if not item.object_key:
        return None
    return get_artifact_storage(item.storage_backend).local_path(item.object_key)


def export_artifact_download_url(item: ProjectExportArtifact | ProjectExportJob, filename: str | None = None) -> str | None:
    download_name = item.filename if isinstance(item, ProjectExportArtifact) else (filename or "项目档案-主卷.zip")
    return get_artifact_storage(item.storage_backend).download_url(item.object_key, download_name) if item.object_key else None


def verify_export_artifact(item: ProjectExportArtifact | ProjectExportJob) -> bool:
    if not item.object_key:
        return False
    expected_sha256 = item.sha256 if isinstance(item, ProjectExportArtifact) else item.artifact_sha256
    expected_size = item.size_bytes if isinstance(item, ProjectExportArtifact) else item.file_size_bytes
    return get_artifact_storage(item.storage_backend).verify(item.object_key, expected_sha256, expected_size)


def delete_export_artifacts(db, job: ProjectExportJob) -> int:
    removed = 0
    artifacts = list(job.artifacts)
    if artifacts:
        for artifact in artifacts:
            try:
                if get_artifact_storage(artifact.storage_backend).delete(artifact.object_key):
                    removed += 1
            finally:
                job.artifacts.remove(artifact)
    elif job.object_key:
        if get_artifact_storage(job.storage_backend).delete(job.object_key):
            removed += 1
    job.object_key = None
    job.artifact_sha256 = None
    job.file_size_bytes = None
    job.part_count = 0
    return removed


def _claim_specific_job(db, job_id: str, worker_id: str) -> ProjectExportJob | None:
    now = datetime.now(timezone.utc)
    job = db.get(ProjectExportJob, job_id)
    if not job or job.status != "queued" or (_aware(job.next_attempt_at) and _aware(job.next_attempt_at) > now):
        return None
    result = db.execute(
        update(ProjectExportJob)
        .where(ProjectExportJob.id == job_id, ProjectExportJob.status == "queued")
        .values(
            status="running",
            attempt_count=ProjectExportJob.attempt_count + 1,
            lease_owner=worker_id,
            lease_expires_at=now + timedelta(seconds=settings.export_job_lease_seconds),
            started_at=now,
            next_attempt_at=None,
            updated_at=now,
        )
    )
    db.commit()
    return db.get(ProjectExportJob, job_id) if result.rowcount == 1 else None


def claim_next_export_job(db, worker_id: str) -> ProjectExportJob | None:
    now = datetime.now(timezone.utc)
    db.execute(
        update(ProjectExportJob)
        .where(
            ProjectExportJob.status == "running",
            ProjectExportJob.lease_expires_at <= now,
            ProjectExportJob.attempt_count >= ProjectExportJob.max_attempts,
        )
        .values(status="dead_letter", stage="重试次数已用尽", finished_at=now, lease_owner=None, lease_expires_at=None)
    )
    db.commit()
    candidate = db.scalar(
        select(ProjectExportJob)
        .where(
            ProjectExportJob.attempt_count < ProjectExportJob.max_attempts,
            or_(
                (ProjectExportJob.status == "queued") & or_(ProjectExportJob.next_attempt_at.is_(None), ProjectExportJob.next_attempt_at <= now),
                (ProjectExportJob.status == "running") & (ProjectExportJob.lease_expires_at <= now),
            ),
        )
        .order_by(ProjectExportJob.created_at)
        .limit(1)
    )
    if not candidate:
        return None
    original_status = candidate.status
    conditions = [ProjectExportJob.id == candidate.id, ProjectExportJob.status == original_status]
    if original_status == "running":
        conditions.append(ProjectExportJob.lease_expires_at <= now)
    result = db.execute(
        update(ProjectExportJob)
        .where(*conditions)
        .values(
            status="running",
            attempt_count=ProjectExportJob.attempt_count + 1,
            lease_owner=worker_id,
            lease_expires_at=now + timedelta(seconds=settings.export_job_lease_seconds),
            started_at=now,
            next_attempt_at=None,
            updated_at=now,
        )
    )
    db.commit()
    return db.get(ProjectExportJob, candidate.id) if result.rowcount == 1 else None


def process_export_job_in_session(db, job_id: str, *, worker_id: str | None = None, claimed: bool = False) -> None:
    staged_paths: list[Path] = []
    stored_artifacts: list[tuple[str, str]] = []
    worker_id = worker_id or _worker_id("inline")
    try:
        job = db.get(ProjectExportJob, job_id) if claimed else _claim_specific_job(db, job_id, worker_id)
        if not job or job.status != "running" or job.lease_owner != worker_id:
            return
        project = db.get(Project, job.project_id)
        if not project:
            job.status = "dead_letter"
            job.error_message = "关联项目不存在"
            job.finished_at = datetime.now(timezone.utc)
            job.lease_owner = None
            job.lease_expires_at = None
            _set_job(job, progress=100, stage="生成失败")
            db.commit()
            return
        if project.status in {"待删除", "删除中"}:
            job.status = "dead_letter"
            job.error_message = "项目正在删除，不能继续生成档案"
            job.finished_at = datetime.now(timezone.utc)
            job.lease_owner = None
            job.lease_expires_at = None
            _set_job(job, progress=100, stage="项目删除中")
            db.commit()
            return

        job.finished_at = None
        job.error_message = None
        job.expires_at = None
        _set_job(job, progress=15, stage="整理项目记录")
        db.commit()

        target_dir = settings.export_dir / ".staging"
        _set_job(job, progress=45, stage="生成报告与档案分卷")
        job.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.export_job_lease_seconds)
        db.commit()
        build = create_project_archive(
            db,
            project,
            target_dir,
            include_attachments=job.include_attachments,
            date_from=job.date_from,
            date_to=job.date_to,
            object_name=f"{job.id}.zip",
        )
        staged_paths = [part.path for part in build.parts]
        db.refresh(project)
        if project.status in {"待删除", "删除中"}:
            raise RuntimeError("项目正在删除，已停止上传档案")
        storage = get_artifact_storage()
        total_size = 0
        first_stored = None
        for part in build.parts:
            object_key = f"{project.id}/{job.id}/part-{part.part_number:02d}.zip"
            stored = storage.store_file(part.path, object_key)
            stored_artifacts.append((stored.backend, stored.object_key))
            if part.path in staged_paths:
                staged_paths.remove(part.path)
            artifact = ProjectExportArtifact(
                job_id=job.id,
                kind=part.kind,
                part_number=part.part_number,
                filename=part.filename,
                object_key=stored.object_key,
                size_bytes=stored.size_bytes,
                sha256=stored.sha256,
                storage_backend=stored.backend,
            )
            job.artifacts.append(artifact)
            total_size += stored.size_bytes
            first_stored = first_stored or stored

        now = datetime.now(timezone.utc)
        job.status = "succeeded"
        job.object_key = first_stored.object_key if first_stored else None
        job.file_size_bytes = total_size
        job.artifact_sha256 = first_stored.sha256 if first_stored else None
        job.storage_backend = first_stored.backend if first_stored else storage.backend
        job.report_version = REPORT_VERSION
        job.report_page_count = build.report_page_count
        job.part_count = len(build.parts)
        job.expires_at = now + timedelta(hours=24)
        job.finished_at = now
        job.lease_owner = None
        job.lease_expires_at = None
        _set_job(job, progress=100, stage="档案已就绪")
        part_detail = f"，共 {len(build.parts)} 个分卷" if len(build.parts) > 1 else ""
        record_event(
            db,
            project_id=project.id,
            event_type="project_export_completed",
            object_type="export",
            object_id=job.id,
            title="项目档案已生成",
            detail=f"正式报告 {build.report_page_count} 页{part_detail}，下载链接 24 小时内有效；{'包含附件' if job.include_attachments else '不含附件'}",
            actor="系统任务",
        )
        create_event_notifications(
            db,
            project,
            code="EXPORT_SUCCEEDED",
            level="info",
            title="项目正式档案已生成",
            message=f"正式报告共 {build.report_page_count} 页{part_detail}，下载链接将在 24 小时后失效。",
            object_type="export",
            object_id=job.id,
            action_path=f"/exports?project={project.id}&job={job.id}",
        )
        db.commit()
    except Exception as exc:
        for path in staged_paths:
            if path.is_file():
                path.unlink()
        staging = settings.export_dir / ".staging"
        if job_id and staging.is_dir():
            for path in staging.glob(f"{job_id}*.zip"):
                if path.parent == staging and (path.is_file() or path.is_symlink()):
                    path.unlink()
        db.rollback()
        for backend, object_key in stored_artifacts:
            try:
                get_artifact_storage(backend).delete(object_key)
            except Exception:
                pass
        job = db.get(ProjectExportJob, job_id)
        if job:
            project = db.get(Project, job.project_id)
            message = str(exc)[:1000]
            terminal = job.attempt_count >= job.max_attempts
            job.status = "dead_letter" if terminal else "queued"
            job.error_message = message
            job.lease_owner = None
            job.lease_expires_at = None
            job.finished_at = datetime.now(timezone.utc) if terminal else None
            delay = settings.export_retry_base_seconds * (2 ** max(0, job.attempt_count - 1))
            job.next_attempt_at = None if terminal else datetime.now(timezone.utc) + timedelta(seconds=delay)
            _set_job(job, progress=100 if terminal else 5, stage="生成失败，需人工重试" if terminal else f"第 {job.attempt_count} 次失败，等待自动重试")
            if project:
                record_event(
                    db,
                    project_id=project.id,
                    event_type="project_export_failed" if terminal else "project_export_retry_scheduled",
                    object_type="export",
                    object_id=job.id,
                    title="项目档案生成失败" if terminal else "项目档案等待自动重试",
                    detail=message,
                    actor="系统任务",
                )
                if terminal:
                    create_event_notifications(
                        db,
                        project,
                        code="EXPORT_FAILED",
                        level="warning",
                        title="项目档案生成失败",
                        message=message,
                        object_type="export",
                        object_id=job.id,
                        action_path=f"/exports?project={project.id}&job={job.id}",
                        event_state="failed",
                    )
            db.commit()
            if not terminal and settings.export_execution_mode == "embedded":
                _schedule_embedded_retry(job.id, delay)


def process_export_job(job_id: str, *, worker_id: str | None = None, claimed: bool = False) -> None:
    db = SessionLocal()
    try:
        process_export_job_in_session(db, job_id, worker_id=worker_id, claimed=claimed)
    finally:
        db.close()
        with scheduled_lock:
            scheduled_job_ids.discard(job_id)


def _schedule_embedded_retry(job_id: str, delay: int) -> None:
    def submit() -> None:
        with scheduled_lock:
            retry_timers.pop(job_id, None)
        submit_export_job(job_id)

    timer = Timer(max(1, delay + 0.25), submit)
    timer.daemon = True
    with scheduled_lock:
        previous = retry_timers.pop(job_id, None)
        if previous:
            previous.cancel()
        retry_timers[job_id] = timer
    timer.start()


def submit_export_job(job_id: str) -> bool:
    global executor
    if settings.export_execution_mode == "worker":
        return True
    with scheduled_lock:
        if job_id in scheduled_job_ids:
            return False
        if executor is None:
            executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="project-export")
        scheduled_job_ids.add(job_id)
        try:
            executor.submit(process_export_job, job_id)
        except RuntimeError:
            scheduled_job_ids.discard(job_id)
            raise
    return True


def run_export_worker_once(worker_id: str) -> bool:
    db = SessionLocal()
    job_id: str | None = None
    try:
        job = claim_next_export_job(db, worker_id)
        if job:
            job_id = job.id
    finally:
        db.close()
    if not job_id:
        touch_worker_with_new_session(worker_id, "export", status="idle")
        return False
    with worker_pulse(worker_id, "export", job_id):
        process_export_job(job_id, worker_id=worker_id, claimed=True)
    db = SessionLocal()
    try:
        result = db.get(ProjectExportJob, job_id)
        failed = bool(result and result.status == "dead_letter")
    finally:
        db.close()
    finish_worker_job(worker_id, "export", failed=failed)
    return True


def purge_expired_export_files() -> int:
    db = SessionLocal()
    removed = 0
    try:
        current = datetime.now(timezone.utc)
        jobs = db.scalars(
            select(ProjectExportJob).where(ProjectExportJob.status == "succeeded", ProjectExportJob.expires_at <= current)
        ).all()
        for job in jobs:
            removed += delete_export_artifacts(db, job)
            job.status = "expired"
            _set_job(job, progress=100, stage="下载链接已过期")
        db.commit()
        return removed
    finally:
        db.close()


def resume_incomplete_export_jobs() -> int:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        jobs = db.scalars(select(ProjectExportJob).where(ProjectExportJob.status.in_({"queued", "running"}))).all()
        resumable: list[tuple[ProjectExportJob, int]] = []
        for job in jobs:
            lease_expired = not _aware(job.lease_expires_at) or _aware(job.lease_expires_at) <= now
            if job.status == "running" and settings.export_execution_mode == "worker" and not lease_expired:
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
            if delay and settings.export_execution_mode == "embedded":
                _schedule_embedded_retry(job.id, delay)
            else:
                submit_export_job(job.id)
        return len(resumable)
    finally:
        db.close()


def shutdown_export_jobs() -> None:
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
