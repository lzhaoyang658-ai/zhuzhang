from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config

from app.core.config import get_settings
from app.services.database_backup import restore_sqlite_backup, sqlite_database_path


logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    settings.validate_runtime_safety()
    database_path = sqlite_database_path()
    if database_path:
        database_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        restore_sqlite_backup()
    except Exception:
        # A temporary storage or IAM failure must not make the whole API
        # unavailable. Start with the local database and keep enough context in
        # the platform logs for operators to repair cloud backup separately.
        logger.exception("SQLite cloud backup restore failed; continuing with local database")

    backend_dir = Path(__file__).resolve().parent
    alembic_config = Config(str(backend_dir / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(backend_dir / "alembic"))
    alembic_config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    command.upgrade(alembic_config, "head")

    port = int(os.getenv("PORT", "8000"))
    trusted_proxies = settings.trusted_proxy_cidrs.strip()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        proxy_headers=bool(trusted_proxies),
        forwarded_allow_ips=trusted_proxies,
    )


if __name__ == "__main__":
    main()
