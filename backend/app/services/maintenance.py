from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Thread

from sqlalchemy import select

from app.core.config import get_settings
from app.database import SessionLocal
from app.models import ProjectExportArtifact, ProjectExportJob
from app.services.artifact_storage import get_artifact_storage
from app.services.export_jobs import purge_expired_export_files
from app.services.project_lifecycle import purge_due_projects


logger = logging.getLogger(__name__)
settings = get_settings()
_stop_event = Event()
_thread: Thread | None = None


def sweep_stale_staging_files(now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(seconds=max(60, settings.maintenance_staging_max_age_seconds))
    staging = settings.export_dir / ".staging"
    if not staging.is_dir():
        return 0
    db = SessionLocal()
    try:
        active_ids = set(db.scalars(select(ProjectExportJob.id).where(ProjectExportJob.status.in_({"queued", "running"}))).all())
    finally:
        db.close()
    removed = 0
    for item in staging.glob("*.zip"):
        job_id = item.name.split("-part-", 1)[0].removesuffix(".zip")
        modified = datetime.fromtimestamp(item.stat().st_mtime, tz=timezone.utc)
        if item.is_file() and job_id not in active_ids and modified <= cutoff:
            item.unlink()
            removed += 1
    return removed


def sweep_orphan_artifact_rows() -> int:
    db = SessionLocal()
    removed = 0
    try:
        orphans = list(db.scalars(
            select(ProjectExportArtifact)
            .outerjoin(ProjectExportJob, ProjectExportArtifact.job_id == ProjectExportJob.id)
            .where(ProjectExportJob.id.is_(None))
        ).all())
        for artifact in orphans:
            get_artifact_storage(artifact.storage_backend).delete(artifact.object_key)
            db.delete(artifact)
            removed += 1
        db.commit()
        return removed
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_maintenance_once() -> dict[str, int]:
    result = {"projects": 0, "expired_exports": 0, "staging_files": 0, "orphan_artifacts": 0, "errors": 0}
    db = SessionLocal()
    try:
        try:
            result["projects"] = len(purge_due_projects(db, settings.upload_dir, export_dir=settings.export_dir))
        except Exception:
            result["errors"] += 1
            logger.exception("Scheduled project purge failed")
    finally:
        db.close()
    for key, operation in (
        ("expired_exports", purge_expired_export_files),
        ("staging_files", sweep_stale_staging_files),
        ("orphan_artifacts", sweep_orphan_artifact_rows),
    ):
        try:
            result[key] = operation()
        except Exception:
            result["errors"] += 1
            logger.exception("Scheduled maintenance operation failed: %s", key)
    return result


def _maintenance_loop() -> None:
    interval = max(60, settings.maintenance_cleanup_interval_seconds)
    while not _stop_event.wait(interval):
        run_maintenance_once()


def start_maintenance_scheduler() -> None:
    global _thread
    if not settings.maintenance_cleanup_enabled or (_thread and _thread.is_alive()):
        return
    _stop_event.clear()
    _thread = Thread(target=_maintenance_loop, name="maintenance-cleanup", daemon=True)
    _thread.start()


def stop_maintenance_scheduler() -> None:
    global _thread
    _stop_event.set()
    if _thread:
        _thread.join(timeout=5)
    _thread = None
