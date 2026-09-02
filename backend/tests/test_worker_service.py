from __future__ import annotations

import time

from app import worker_service


def wait_until(predicate, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("condition was not reached before timeout")


def test_worker_runtime_runs_both_queues_and_stops(monkeypatch):
    calls = {"quote": 0, "export": 0}

    def quote_once(_worker_id: str) -> bool:
        calls["quote"] += 1
        return calls["quote"] == 1

    def export_once(_worker_id: str) -> bool:
        calls["export"] += 1
        return False

    monkeypatch.setattr(worker_service.settings, "worker_queues", "quote,export")
    monkeypatch.setattr(worker_service.settings, "quote_worker_poll_seconds", .01)
    monkeypatch.setattr(worker_service.settings, "export_worker_poll_seconds", .01)
    monkeypatch.setattr(worker_service, "run_quote_worker_once", quote_once)
    monkeypatch.setattr(worker_service, "run_export_worker_once", export_once)
    monkeypatch.setattr(worker_service, "purge_expired_export_files", lambda: 0)
    runtime = worker_service.WorkerRuntime()
    runtime.start()
    wait_until(lambda: calls["quote"] >= 2 and calls["export"] >= 1)
    snapshot = runtime.snapshot()
    runtime.stop()

    assert snapshot["quote"]["processed"] == 1
    assert snapshot["quote"]["last_error"] is None
    assert snapshot["export"]["cycles"] >= 1
    assert all(not thread.is_alive() for thread in runtime.threads.values())


def test_worker_runtime_recovers_after_runner_error(monkeypatch):
    calls = 0

    def flaky(_worker_id: str) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary database failure")
        return False

    monkeypatch.setattr(worker_service.settings, "worker_queues", "quote")
    monkeypatch.setattr(worker_service.settings, "worker_failure_backoff_seconds", .01)
    monkeypatch.setattr(worker_service.settings, "quote_worker_poll_seconds", .01)
    monkeypatch.setattr(worker_service, "run_quote_worker_once", flaky)
    runtime = worker_service.WorkerRuntime()
    runtime.start()
    wait_until(lambda: calls >= 2, timeout=2.0)
    snapshot = runtime.snapshot()
    runtime.stop()

    assert snapshot["quote"]["cycles"] >= 2
    assert snapshot["quote"]["last_error"] is None


def test_database_readiness_can_require_postgresql(monkeypatch):
    monkeypatch.setattr(worker_service.settings, "worker_require_postgresql", True)
    monkeypatch.setattr(worker_service.settings, "database_url", "sqlite:///./data/app.db")
    available, reason, schema = worker_service.database_ready()

    assert available is False
    assert reason == "production worker requires PostgreSQL"
    assert schema is None


def test_worker_runtime_rejects_unknown_queue(monkeypatch):
    monkeypatch.setattr(worker_service.settings, "worker_queues", "quote,unknown")
    try:
        worker_service.WorkerRuntime()
    except ValueError as exc:
        assert "unknown" in str(exc)
    else:
        raise AssertionError("invalid worker queue should fail fast")
