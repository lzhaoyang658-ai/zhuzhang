from __future__ import annotations

import argparse
import signal
import time

from app.core.config import get_settings
from app.database import engine
from app.services.export_jobs import _worker_id, purge_expired_export_files, run_export_worker_once
from app.services.schema_version import assert_database_schema_current


settings = get_settings()
running = True


def stop(*_: object) -> None:
    global running
    running = False


def main() -> None:
    settings.validate_runtime_safety()
    if settings.app_env != "test":
        assert_database_schema_current(engine)
    parser = argparse.ArgumentParser(description="装修项目档案独立 Worker")
    parser.add_argument("--once", action="store_true", help="最多处理一个任务后退出")
    parser.add_argument("--purge-expired", action="store_true", help="启动时清理过期档案")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker_id = _worker_id("export-worker")
    if args.purge_expired:
        purge_expired_export_files()
    while running:
        processed = run_export_worker_once(worker_id)
        if args.once:
            return
        if not processed:
            time.sleep(max(0.25, settings.export_worker_poll_seconds))


if __name__ == "__main__":
    main()
