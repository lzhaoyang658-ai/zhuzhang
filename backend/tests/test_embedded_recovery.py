from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.models import Project, ProjectExportJob, Quote, QuoteParseJob
from app.services import export_jobs, quote_jobs


def _project_and_quote(db_session):
    project = Project(name="恢复测试", city="北京", area_sqm=80, fund_limit_cents=200_000_00)
    db_session.add(project)
    db_session.flush()
    quote = Quote(
        project_id=project.id,
        name="恢复报价",
        original_name="恢复报价.csv",
        object_key="recovery.csv",
        status="queued",
    )
    db_session.add(quote)
    db_session.flush()
    return project, quote


def test_quote_recovery_preserves_future_retry_delay(db_session, monkeypatch):
    project, quote = _project_and_quote(db_session)
    retry_at = datetime.now(timezone.utc) + timedelta(seconds=60)
    job = QuoteParseJob(project_id=project.id, quote_id=quote.id, status="queued", next_attempt_at=retry_at)
    db_session.add(job)
    db_session.commit()
    job_id = job.id
    scheduled: list[tuple[str, int]] = []

    monkeypatch.setattr(get_settings(), "quote_execution_mode", "embedded")
    monkeypatch.setattr(quote_jobs, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(quote_jobs, "_schedule_embedded_retry", lambda job_id, delay: scheduled.append((job_id, delay)))

    assert quote_jobs.resume_incomplete_jobs() == 1
    job = db_session.get(QuoteParseJob, job_id)
    assert job.next_attempt_at is not None
    assert job.stage == "服务恢复，等待自动重试"
    assert scheduled and scheduled[0][0] == job.id and 1 <= scheduled[0][1] <= 60


def test_export_recovery_preserves_future_retry_delay(db_session, monkeypatch):
    project, _ = _project_and_quote(db_session)
    retry_at = datetime.now(timezone.utc) + timedelta(seconds=60)
    job = ProjectExportJob(
        project_id=project.id,
        requested_by_user_id="demo-owner",
        status="queued",
        next_attempt_at=retry_at,
    )
    db_session.add(job)
    db_session.commit()
    job_id = job.id
    scheduled: list[tuple[str, int]] = []

    monkeypatch.setattr(get_settings(), "export_execution_mode", "embedded")
    monkeypatch.setattr(export_jobs, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(export_jobs, "_schedule_embedded_retry", lambda job_id, delay: scheduled.append((job_id, delay)))

    assert export_jobs.resume_incomplete_export_jobs() == 1
    job = db_session.get(ProjectExportJob, job_id)
    assert job.next_attempt_at is not None
    assert job.stage == "服务恢复，等待自动重试"
    assert scheduled and scheduled[0][0] == job.id and 1 <= scheduled[0][1] <= 60
