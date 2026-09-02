from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.core.config import BACKEND_ROOT, get_settings
from app.database import Base
from app.services.schema_version import (assert_database_schema_current,
                                         database_schema_status,
                                         expected_schema_revisions)


def memory_engine():
    return create_engine("sqlite://", poolclass=StaticPool)


def test_create_all_database_is_allowed_only_as_unmanaged_test_database():
    engine = memory_engine()
    Base.metadata.create_all(engine)

    with engine.connect() as connection:
        regular = database_schema_status(connection)
        test_database = database_schema_status(connection, allow_unmanaged_test_database=True)

    assert regular.ready is False
    assert regular.current_revisions == ()
    assert test_database.ready is True
    assert test_database.unmanaged_test_database is True


def test_current_and_stale_alembic_revisions_are_distinguished():
    expected = expected_schema_revisions()
    assert expected
    engine = memory_engine()
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        connection.exec_driver_sql("INSERT INTO alembic_version (version_num) VALUES (?)", (expected[0],))
    with engine.connect() as connection:
        assert database_schema_status(connection).ready is True

    with engine.begin() as connection:
        connection.exec_driver_sql("UPDATE alembic_version SET version_num = 'stale-revision'")
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        assert_database_schema_current(engine)


def test_readiness_reports_stale_schema(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "app_env", "development")

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["schema"]["ready"] is False
    assert "alembic upgrade head" in response.json()["schema"]["error"]


def test_test_storage_fixture_does_not_target_repository_data(isolate_application_storage):
    settings = get_settings()

    assert settings.upload_dir.is_relative_to(isolate_application_storage)
    assert settings.export_dir.is_relative_to(isolate_application_storage)
    assert not settings.upload_dir.is_relative_to(BACKEND_ROOT / "data")
    assert not settings.export_dir.is_relative_to(BACKEND_ROOT / "data")
