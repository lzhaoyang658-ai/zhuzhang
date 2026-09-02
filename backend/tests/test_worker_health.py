from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.database import engine_options
from app.models import Project, ProjectExportJob, Quote, QuoteParseJob, WorkerHeartbeat
from app.services.quote_jobs import claim_next_quote_job
from app.services.worker_health import queue_health_snapshot, touch_worker


def test_postgresql_engine_options_enable_liveness_and_pooling(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "database_pool_size", 7)
    monkeypatch.setattr(settings, "database_max_overflow", 4)
    monkeypatch.setattr(settings, "database_pool_recycle_seconds", 240)
    options = engine_options("postgresql+psycopg://user:secret@db.example/app")
    assert options["pool_pre_ping"] is True
    assert options["pool_size"] == 7
    assert options["max_overflow"] == 4
    assert options["pool_recycle"] == 240
    assert options["connect_args"]["connect_timeout"] == settings.database_connect_timeout_seconds


def test_quote_worker_reclaims_lease_and_dead_letters(db_session):
    project = Project(id="project", name="Worker 测试", city="苏州", area_sqm=80, fund_limit_cents=100_000_00)
    quote = Quote(id="quote", project_id=project.id, name="报价", original_name="quote.csv", object_key="quote.csv")
    job = QuoteParseJob(
        project_id="project",
        quote_id="quote",
        max_attempts=2,
    )
    db_session.add_all([project, quote, job])
    db_session.commit()
    claimed = claim_next_quote_job(db_session, "quote-a")
    assert claimed and claimed.id == job.id and claimed.attempt == 1
    claimed.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    reclaimed = claim_next_quote_job(db_session, "quote-b")
    assert reclaimed and reclaimed.attempt == 2 and reclaimed.lease_owner == "quote-b"
    reclaimed.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    assert claim_next_quote_job(db_session, "quote-c") is None
    db_session.refresh(job)
    assert job.status == "dead_letter" and job.lease_owner is None


def test_worker_heartbeat_and_scoped_queue_health(db_session, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "quote_execution_mode", "worker")
    monkeypatch.setattr(settings, "export_execution_mode", "worker")
    visible = Project(id="visible", name="可见项目", city="苏州", area_sqm=80, fund_limit_cents=100_000_00)
    hidden = Project(id="hidden", name="隐藏项目", city="苏州", area_sqm=80, fund_limit_cents=100_000_00)
    quote = Quote(id="quote", project_id=visible.id, name="报价", original_name="quote.csv", object_key="quote.csv")
    quote_job = QuoteParseJob(project_id=visible.id, quote_id=quote.id, status="queued")
    hidden_export = ProjectExportJob(project_id=hidden.id, requested_by_user_id="demo-owner", status="dead_letter")
    db_session.add_all([visible, hidden, quote, quote_job, hidden_export])
    db_session.commit()
    touch_worker(db_session, "quote-worker:test", "quote", status="busy", current_job_id=quote_job.id)
    touch_worker(db_session, "export-worker:test", "export", status="idle")

    snapshot = queue_health_snapshot(db_session, {"visible"})
    assert snapshot["quote"]["status"] == "healthy"
    assert snapshot["quote"]["workers_busy"] == 1
    assert snapshot["quote"]["queued"] == 1
    assert snapshot["export"]["dead_letter"] == 0
    heartbeat = db_session.get(WorkerHeartbeat, "quote-worker:test")
    assert heartbeat and heartbeat.current_job_id == quote_job.id
