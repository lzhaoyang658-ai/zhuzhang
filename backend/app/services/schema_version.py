from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection, Engine

from app.core.config import BACKEND_ROOT


@dataclass(frozen=True)
class DatabaseSchemaStatus:
    ready: bool
    current_revisions: tuple[str, ...]
    expected_revisions: tuple[str, ...]
    unmanaged_test_database: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


@lru_cache
def expected_schema_revisions() -> tuple[str, ...]:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return tuple(sorted(ScriptDirectory.from_config(config).get_heads()))


def database_schema_status(
    connection: Connection,
    *,
    allow_unmanaged_test_database: bool = False,
) -> DatabaseSchemaStatus:
    current = tuple(sorted(MigrationContext.configure(connection).get_current_heads()))
    expected = expected_schema_revisions()
    unmanaged_test_database = allow_unmanaged_test_database and not current
    return DatabaseSchemaStatus(
        ready=bool(expected) and (current == expected or unmanaged_test_database),
        current_revisions=current,
        expected_revisions=expected,
        unmanaged_test_database=unmanaged_test_database,
    )


def database_schema_status_for_engine(
    engine: Engine,
    *,
    allow_unmanaged_test_database: bool = False,
) -> DatabaseSchemaStatus:
    with engine.connect() as connection:
        return database_schema_status(
            connection,
            allow_unmanaged_test_database=allow_unmanaged_test_database,
        )


def schema_mismatch_message(status: DatabaseSchemaStatus) -> str:
    current = ",".join(status.current_revisions) or "none"
    expected = ",".join(status.expected_revisions) or "none"
    return (
        "Database schema is not at the Alembic head "
        f"(current={current}, expected={expected}). Run `alembic upgrade head` before starting the service."
    )


def assert_database_schema_current(engine: Engine) -> DatabaseSchemaStatus:
    status = database_schema_status_for_engine(engine)
    if not status.ready:
        raise RuntimeError(schema_mismatch_message(status))
    return status
