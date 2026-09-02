from __future__ import annotations

import argparse
import os
from collections.abc import Iterable
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url

from app.core.config import normalize_database_url
from app.database import Base, enable_sqlite_foreign_keys
import app.models  # noqa: F401  # register every mapped table in Base.metadata


def upgrade_target(database_url: str) -> None:
    database_url = normalize_database_url(database_url)
    backend_dir = Path(__file__).resolve().parent
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def table_counts(engine: Engine, tables: Iterable) -> dict[str, int]:
    with engine.connect() as connection:
        return {table.name: int(connection.scalar(select(func.count()).select_from(table)) or 0) for table in tables}


def copy_database(source: Engine, target: Engine, *, batch_size: int = 500) -> dict[str, int]:
    tables = list(Base.metadata.sorted_tables)
    missing = sorted({table.name for table in tables} - set(inspect(source).get_table_names()))
    if missing:
        raise RuntimeError(f"source database is missing tables: {', '.join(missing)}")
    existing = {name: count for name, count in table_counts(target, tables).items() if count}
    if existing:
        detail = ", ".join(f"{name}={count}" for name, count in sorted(existing.items()))
        raise RuntimeError(f"target database must be empty before cutover: {detail}")

    copied: dict[str, int] = {}
    with source.connect() as source_connection, target.begin() as target_connection:
        for table in tables:
            count = 0
            result = source_connection.execute(select(table))
            while rows := result.mappings().fetchmany(batch_size):
                target_connection.execute(table.insert(), [dict(row) for row in rows])
                count += len(rows)
            copied[table.name] = count
        if target.dialect.name == "postgresql":
            reset_postgresql_sequences(target_connection, tables)

    actual = table_counts(target, tables)
    mismatched = {name: (count, actual.get(name, 0)) for name, count in copied.items() if actual.get(name, 0) != count}
    if mismatched:
        detail = ", ".join(f"{name}: source={pair[0]} target={pair[1]}" for name, pair in sorted(mismatched.items()))
        raise RuntimeError(f"row-count validation failed: {detail}")
    return copied


def reset_postgresql_sequences(connection, tables: Iterable) -> None:
    preparer = connection.dialect.identifier_preparer
    for table in tables:
        for column in table.primary_key.columns:
            try:
                python_type = column.type.python_type
            except (AttributeError, NotImplementedError):
                continue
            if python_type is not int:
                continue
            sequence = connection.scalar(
                text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
                {"table_name": table.name, "column_name": column.name},
            )
            if sequence:
                quoted_table = preparer.quote(table.name)
                quoted_column = preparer.quote(column.name)
                connection.execute(
                    text(
                        f"SELECT setval(CAST(:sequence AS regclass), COALESCE(MAX({quoted_column}), 1), "
                        f"MAX({quoted_column}) IS NOT NULL) FROM {quoted_table}"
                    ),
                    {"sequence": sequence},
                )


def validate_urls(source_url: str, target_url: str) -> None:
    source = make_url(source_url)
    target = make_url(target_url)
    if not source.drivername.startswith("sqlite"):
        raise RuntimeError("SOURCE_DATABASE_URL must point to the current SQLite database")
    if not target.drivername.startswith("postgresql"):
        raise RuntimeError("DATABASE_URL must point to PostgreSQL for production cutover")
    if str(source) == str(target):
        raise RuntimeError("source and target databases must be different")


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy the current SQLite ledger into an empty PostgreSQL database")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be positive")
    source_url = normalize_database_url(os.getenv("SOURCE_DATABASE_URL", "sqlite:///./data/app.db"))
    target_url = normalize_database_url(os.getenv("DATABASE_URL", "")) if os.getenv("DATABASE_URL") else ""
    if not target_url:
        raise SystemExit("DATABASE_URL is required and must be provided through the environment")
    validate_urls(source_url, target_url)
    upgrade_target(target_url)
    source_engine = create_engine(source_url, pool_pre_ping=True)
    enable_sqlite_foreign_keys(source_engine)
    target_engine = create_engine(target_url, pool_pre_ping=True)
    copied = copy_database(source_engine, target_engine, batch_size=args.batch_size)
    total = sum(copied.values())
    nonempty = sum(1 for count in copied.values() if count)
    print(f"Cutover copy complete: {total} rows across {nonempty} non-empty tables; row counts verified.")


if __name__ == "__main__":
    main()
