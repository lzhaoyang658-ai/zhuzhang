from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (AcceptanceRecord, AuditEvent, BaselineVersion, ChangeOrder,
                        DeletedProjectRecord, Evidence, PaymentMilestone, PaymentRecord,
                        Project, ProjectBudgetCategory, ProjectFundLimitHistory,
                        ProjectInvite, ProjectMembership, Quote, QuoteCorrection,
                        QuoteItem, QuoteMatchGroup, QuoteMatchMember, QuoteParseJob,
                        Notification, ProjectExportArtifact, ProjectExportJob)
from app.services.auth import secure_hash
from app.services.artifact_storage import get_artifact_storage
from app.services.source_storage import get_source_storage


settings = get_settings()


def _remove_project_directory(root: Path, project_id: str) -> None:
    project_dir = (root / project_id).resolve()
    safe_root = root.resolve()
    if project_dir.parent != safe_root or not project_dir.exists():
        return
    for item in sorted(project_dir.rglob("*"), key=lambda path: len(path.parts), reverse=True):
        if item.is_file() or item.is_symlink():
            item.unlink()
        elif item.is_dir():
            item.rmdir()
    project_dir.rmdir()


def _remove_staging_files(root: Path, job_ids: list[str]) -> None:
    staging = (root / ".staging").resolve()
    safe_root = root.resolve()
    if staging.parent != safe_root or not staging.is_dir():
        return
    for job_id in job_ids:
        for item in staging.glob(f"{job_id}*.zip"):
            if item.parent == staging and (item.is_file() or item.is_symlink()):
                item.unlink()


def purge_due_projects(db: Session, upload_dir: Path, now: datetime | None = None, export_dir: Path | None = None) -> list[str]:
    current = now or datetime.now(timezone.utc)
    projects = db.scalars(select(Project).where(
        Project.status.in_({"待删除", "删除中"}),
        Project.deletion_scheduled_for <= current,
    )).all()
    deleted_ids: list[str] = []
    failures: list[str] = []
    for project in projects:
        try:
            if project.status != "删除中":
                project.status = "删除中"
                db.commit()

            owner = db.scalar(select(ProjectMembership).where(ProjectMembership.project_id == project.id, ProjectMembership.role == "owner"))
            attachment_count = db.scalar(select(func.count(Evidence.id)).where(Evidence.project_id == project.id)) or 0
            count_models = [BaselineVersion, ChangeOrder, PaymentMilestone, PaymentRecord, AcceptanceRecord, Evidence, Quote, QuoteItem, AuditEvent]
            business_count = sum((db.scalar(select(func.count(model.id)).where(model.project_id == project.id)) or 0) for model in count_models)
            jobs = list(db.scalars(select(ProjectExportJob).where(ProjectExportJob.project_id == project.id)).all())
            job_ids = [job.id for job in jobs]
            artifacts = list(db.scalars(select(ProjectExportArtifact).where(ProjectExportArtifact.job_id.in_(job_ids))).all()) if job_ids else []

            source_storage = get_source_storage()
            source_storage.delete_project(project.id)
            _remove_project_directory(upload_dir, project.id)
            artifact_backends = {settings.artifact_storage_backend}
            artifact_backends.update(job.storage_backend for job in jobs if job.storage_backend)
            artifact_backends.update(artifact.storage_backend for artifact in artifacts if artifact.storage_backend)
            for artifact in artifacts:
                get_artifact_storage(artifact.storage_backend).delete(artifact.object_key)
            for job in jobs:
                if job.object_key and not any(item.object_key == job.object_key for item in artifacts):
                    get_artifact_storage(job.storage_backend).delete(job.object_key)
            for backend in artifact_backends:
                get_artifact_storage(backend).delete_project(project.id)

            resolved_export_dir = export_dir or settings.export_dir
            _remove_project_directory(resolved_export_dir, project.id)
            _remove_staging_files(resolved_export_dir, job_ids)

            db.add(DeletedProjectRecord(
                project_reference_hash=secure_hash(f"deleted-project:{project.id}"),
                owner_user_id=owner.user_id if owner else "unknown",
                requested_at=project.deletion_requested_at or current,
                deleted_at=current,
                business_record_count=business_count,
                attachment_count=attachment_count,
            ))
            if job_ids:
                db.execute(delete(ProjectExportArtifact).where(ProjectExportArtifact.job_id.in_(job_ids)))
            ordered_models = [
                QuoteMatchMember, QuoteMatchGroup, QuoteCorrection, QuoteParseJob, QuoteItem,
                AcceptanceRecord, PaymentRecord, Evidence, BaselineVersion, Quote, PaymentMilestone,
                ChangeOrder, ProjectInvite, ProjectBudgetCategory, Notification, ProjectExportJob,
                ProjectFundLimitHistory, AuditEvent, ProjectMembership,
            ]
            for model in ordered_models:
                db.execute(delete(model).where(model.project_id == project.id))
            db.execute(delete(Project).where(Project.id == project.id))
            db.commit()
            deleted_ids.append(project.id)
        except Exception as exc:
            db.rollback()
            failures.append(f"{project.id}: {exc}")
    if failures:
        raise RuntimeError("项目到期删除未完全成功：" + "；".join(failures))
    return deleted_ids
