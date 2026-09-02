from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from threading import Event, Lock, Thread
from typing import Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.database import SessionLocal, engine
from app.services.export_jobs import _worker_id as export_worker_id
from app.services.export_jobs import purge_expired_export_files, run_export_worker_once
from app.services.quote_jobs import _worker_id as quote_worker_id
from app.services.quote_jobs import run_quote_worker_once
from app.services.schema_version import (assert_database_schema_current,
                                         database_schema_status,
                                         schema_mismatch_message)


settings = get_settings()
RunOnce = Callable[[str], bool]


class WorkerRuntime:
    def __init__(self) -> None:
        requested = [item.strip().lower() for item in settings.worker_queues.split(",") if item.strip()]
        invalid = sorted(set(requested) - {"quote", "export"})
        if invalid:
            raise ValueError(f"Unsupported worker queues: {', '.join(invalid)}")
        self.queues = list(dict.fromkeys(requested))
        if not self.queues:
            raise ValueError("WORKER_QUEUES must enable quote, export, or both")
        self.stop_event = Event()
        self.lock = Lock()
        self.threads: dict[str, Thread] = {}
        self.state: dict[str, dict] = {
            queue: {"cycles": 0, "processed": 0, "last_error": None, "last_cycle_at": None}
            for queue in self.queues
        }

    def start(self) -> None:
        if "export" in self.queues:
            try:
                purge_expired_export_files()
            except Exception as exc:
                self._record("export", error=exc)
        runners: dict[str, tuple[RunOnce, str, float]] = {
            "quote": (run_quote_worker_once, quote_worker_id("quote-worker-service"), settings.quote_worker_poll_seconds),
            "export": (run_export_worker_once, export_worker_id("export-worker-service"), settings.export_worker_poll_seconds),
        }
        for queue in self.queues:
            run_once, worker_id, poll_seconds = runners[queue]
            thread = Thread(
                target=self._run_loop,
                args=(queue, worker_id, run_once, poll_seconds),
                name=f"{queue}-worker-loop",
                daemon=True,
            )
            self.threads[queue] = thread
            thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        for thread in self.threads.values():
            thread.join(timeout=max(1, settings.worker_shutdown_seconds))

    def _record(self, queue: str, *, processed: bool = False, error: Exception | None = None) -> None:
        with self.lock:
            item = self.state[queue]
            item["cycles"] += 1
            item["processed"] += int(processed)
            item["last_cycle_at"] = datetime.now(timezone.utc).isoformat()
            item["last_error"] = str(error)[:500] if error else None

    def _run_loop(self, queue: str, worker_id: str, run_once: RunOnce, poll_seconds: float) -> None:
        while not self.stop_event.is_set():
            try:
                processed = run_once(worker_id)
                self._record(queue, processed=processed)
                if not processed:
                    self.stop_event.wait(max(.25, poll_seconds))
            except Exception as exc:
                self._record(queue, error=exc)
                self.stop_event.wait(max(1.0, settings.worker_failure_backoff_seconds))

    def snapshot(self) -> dict:
        with self.lock:
            state = {queue: dict(item) for queue, item in self.state.items()}
        for queue, item in state.items():
            item["thread_alive"] = bool(self.threads.get(queue) and self.threads[queue].is_alive())
        return state


def database_ready() -> tuple[bool, str | None, dict | None]:
    if settings.worker_require_postgresql and settings.is_sqlite:
        return False, "production worker requires PostgreSQL", None
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        schema = database_schema_status(
            db.connection(),
            allow_unmanaged_test_database=settings.app_env == "test",
        )
        if not schema.ready:
            return False, schema_mismatch_message(schema), schema.as_dict()
        return True, None, schema.as_dict()
    except Exception as exc:
        return False, str(exc)[:500], None
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_runtime_safety()
    if settings.app_env != "test":
        assert_database_schema_current(engine)
    runtime = WorkerRuntime()
    app.state.runtime = runtime
    runtime.start()
    try:
        yield
    finally:
        runtime.stop()


app = FastAPI(title="筑账任务 Worker", version="1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "alive", "service": "worker"}


@app.get("/ready")
def ready() -> JSONResponse:
    runtime: WorkerRuntime = app.state.runtime
    queues = runtime.snapshot()
    database_ok, database_error, schema = database_ready()
    threads_ok = bool(queues) and all(item["thread_alive"] for item in queues.values())
    ready_now = database_ok and threads_ok
    return JSONResponse(
        status_code=200 if ready_now else 503,
        content={
            "status": "ready" if ready_now else "degraded",
            "database": "available" if database_ok else "unavailable",
            "database_error": database_error,
            "schema": schema,
            "queues": queues,
        },
    )
