from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings, sqlite_database_path_from_url


settings = get_settings()
db_path = sqlite_database_path_from_url(settings.database_url)
if db_path:
    db_path.parent.mkdir(parents=True, exist_ok=True)

def engine_options(database_url: str) -> dict:
    options: dict = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
    else:
        options.update({
            "pool_size": settings.database_pool_size,
            "max_overflow": settings.database_max_overflow,
            "pool_recycle": settings.database_pool_recycle_seconds,
        })
        if database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            options["connect_args"] = {"connect_timeout": settings.database_connect_timeout_seconds}
    return options


def enable_sqlite_foreign_keys(target: Engine) -> Engine:
    if target.dialect.name != "sqlite":
        return target

    @event.listens_for(target, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return target


engine = create_engine(settings.database_url, **engine_options(settings.database_url))
enable_sqlite_foreign_keys(engine)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
