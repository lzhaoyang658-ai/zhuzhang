from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.models import (Project, ProjectExportArtifact, ProjectExportJob,
                        ProjectMembership)
from app.services.project_lifecycle import purge_due_projects


def due_project(db_session) -> Project:
    project = Project(
        name="待删除项目",
        city="上海",
        area_sqm=80,
        fund_limit_cents=200_000_00,
        status="待删除",
        deletion_requested_at=datetime.now(timezone.utc) - timedelta(days=8),
        deletion_scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db_session.add(project)
    db_session.flush()
    db_session.add(ProjectMembership(user_id="demo-owner", project_id=project.id, role="owner"))
    db_session.commit()
    return project


def test_project_purge_removes_artifact_rows_bytes_cache_and_staging(db_session, monkeypatch, tmp_path):
    settings = get_settings()
    upload_dir = tmp_path / "uploads"
    export_dir = tmp_path / "exports"
    monkeypatch.setattr(settings, "source_storage_backend", "local")
    monkeypatch.setattr(settings, "artifact_storage_backend", "local")
    monkeypatch.setattr(settings, "upload_dir", upload_dir)
    monkeypatch.setattr(settings, "export_dir", export_dir)
    project = due_project(db_session)

    job = ProjectExportJob(
        project_id=project.id,
        requested_by_user_id="demo-owner",
        status="succeeded",
        object_key=f"{project.id}/legacy.zip",
        storage_backend="local",
    )
    db_session.add(job)
    db_session.flush()
    artifact = ProjectExportArtifact(
        job_id=job.id,
        filename="项目档案.zip",
        object_key=f"{project.id}/{job.id}/part-01.zip",
        size_bytes=7,
        sha256="a" * 64,
        storage_backend="local",
    )
    db_session.add(artifact)
    db_session.commit()

    source_file = upload_dir / project.id / "cached.pdf"
    artifact_file = export_dir / artifact.object_key
    legacy_file = export_dir / job.object_key
    staging_file = export_dir / ".staging" / f"{job.id}.zip"
    for path in (source_file, artifact_file, legacy_file, staging_file):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"archive")

    assert purge_due_projects(db_session, upload_dir, export_dir=export_dir) == [project.id]
    assert db_session.get(Project, project.id) is None
    assert db_session.get(ProjectExportJob, job.id) is None
    assert db_session.get(ProjectExportArtifact, artifact.id) is None
    assert not source_file.exists()
    assert not artifact_file.exists()
    assert not legacy_file.exists()
    assert not staging_file.exists()


def test_failed_storage_purge_keeps_a_retryable_deleting_project(db_session, monkeypatch, tmp_path):
    project = due_project(db_session)

    class BrokenStorage:
        def delete_project(self, _project_id):
            raise RuntimeError("storage unavailable")

    monkeypatch.setattr("app.services.project_lifecycle.get_source_storage", lambda: BrokenStorage())
    with pytest.raises(RuntimeError, match="storage unavailable"):
        purge_due_projects(db_session, tmp_path)

    db_session.expire_all()
    assert db_session.get(Project, project.id).status == "删除中"
