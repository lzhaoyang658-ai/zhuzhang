from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.pool import StaticPool

from app.database import Base, enable_sqlite_foreign_keys
from app.models import User
from database_cutover import copy_database, validate_urls


def memory_engine():
    return enable_sqlite_foreign_keys(create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool))


def test_copy_database_preserves_rows_and_rejects_nonempty_target():
    source = memory_engine()
    target = memory_engine()
    Base.metadata.create_all(source)
    Base.metadata.create_all(target)
    with source.begin() as connection:
        connection.execute(User.__table__.insert(), [{"id": "cutover-user", "name": "迁移验收", "email": "cutover@example.local", "status": "active"}])

    copied = copy_database(source, target, batch_size=1)
    with target.connect() as connection:
        user = connection.execute(select(User.__table__).where(User.id == "cutover-user")).mappings().one()

    assert copied["users"] == 1
    assert user["name"] == "迁移验收"
    with pytest.raises(RuntimeError, match="target database must be empty"):
        copy_database(source, target)


def test_sqlite_engines_enable_foreign_key_enforcement():
    engine = memory_engine()
    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1


def test_validate_urls_requires_sqlite_to_postgresql():
    validate_urls("sqlite:///./data/app.db", "postgresql+psycopg://app:secret@db.internal/app")
    with pytest.raises(RuntimeError, match="SOURCE_DATABASE_URL"):
        validate_urls("postgresql://source/db", "postgresql://target/db")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        validate_urls("sqlite:///source.db", "sqlite:///target.db")
