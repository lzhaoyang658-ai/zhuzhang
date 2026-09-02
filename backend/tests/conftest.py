import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, enable_sqlite_foreign_keys, get_db
from app.core.config import get_settings
from app.main import app
from app.models import User


@pytest.fixture(autouse=True)
def isolate_application_storage(tmp_path, monkeypatch):
    """Never let tests write uploads or exports into the repository data tree."""
    settings = get_settings()
    storage_root = tmp_path / "application-storage"
    monkeypatch.setattr(settings, "upload_dir", storage_root / "uploads")
    monkeypatch.setattr(settings, "export_dir", storage_root / "exports")
    yield storage_root


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    enable_sqlite_foreign_keys(engine)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(User(id="demo-owner", name="林然", email="owner@example.local"))
    session.commit()
    yield session
    session.close()


@pytest.fixture()
def client(db_session, monkeypatch):
    def override_db():
        yield db_session
    monkeypatch.setattr("app.main.submit_quote_job", lambda _job_id: True)
    monkeypatch.setattr("app.main.submit_export_job", lambda _job_id: True)
    # Lifespan recovery and maintenance use the process-wide database rather
    # than FastAPI's dependency override. Keep API tests fully isolated from it.
    monkeypatch.setattr("app.main.resume_incomplete_jobs", lambda: 0)
    monkeypatch.setattr("app.main.resume_incomplete_export_jobs", lambda: 0)
    monkeypatch.setattr("app.main.run_maintenance_once", lambda: {})
    monkeypatch.setattr("app.main.start_database_backup_scheduler", lambda: False)
    monkeypatch.setattr("app.main.start_maintenance_scheduler", lambda: False)
    monkeypatch.setattr("app.main.stop_database_backup_scheduler", lambda: None)
    monkeypatch.setattr("app.main.stop_maintenance_scheduler", lambda: None)
    monkeypatch.setattr("app.main.prune_stale_worker_heartbeats", lambda _db: 0)
    monkeypatch.setattr(get_settings(), "app_env", "test")
    monkeypatch.setattr(get_settings(), "auth_allow_demo_header", True)
    monkeypatch.setattr(get_settings(), "seed_demo_enabled", False)
    app.dependency_overrides[get_db] = override_db
    with TestClient(app, headers={"X-Demo-User-Id": "demo-owner"}) as test_client:
        yield test_client
    app.dependency_overrides.clear()
