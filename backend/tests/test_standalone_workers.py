from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    ("module_name", "poll_setting", "runner_name"),
    [
        ("quote_worker", "quote_worker_poll_seconds", "run_quote_worker_once"),
        ("export_worker", "export_worker_poll_seconds", "run_export_worker_once"),
    ],
)
@pytest.mark.parametrize(("app_env", "expects_schema_check"), [("development", True), ("test", False)])
def test_standalone_worker_startup_checks_runtime_and_schema(
    monkeypatch,
    module_name: str,
    poll_setting: str,
    runner_name: str,
    app_env: str,
    expects_schema_check: bool,
):
    module = importlib.import_module(module_name)
    calls = {"runtime": 0, "schema": 0, "runner": 0}
    settings = SimpleNamespace(
        app_env=app_env,
        validate_runtime_safety=lambda: calls.__setitem__("runtime", calls["runtime"] + 1),
        **{poll_setting: 0.25},
    )
    monkeypatch.setattr(module, "settings", settings)
    monkeypatch.setattr(
        module,
        "assert_database_schema_current",
        lambda received_engine: calls.__setitem__("schema", calls["schema"] + int(received_engine is module.engine)),
    )
    monkeypatch.setattr(module, "_worker_id", lambda *_args: "test-worker")
    monkeypatch.setattr(
        module,
        runner_name,
        lambda worker_id: calls.__setitem__("runner", calls["runner"] + int(worker_id == "test-worker")) or False,
    )
    monkeypatch.setattr(module.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(module, "running", True)
    monkeypatch.setattr("sys.argv", [f"{module_name}.py", "--once"])

    module.main()

    assert calls == {
        "runtime": 1,
        "schema": int(expects_schema_check),
        "runner": 1,
    }


@pytest.mark.parametrize("module_name", ["quote_worker", "export_worker"])
def test_standalone_worker_does_not_process_when_schema_is_stale(monkeypatch, module_name: str):
    module = importlib.import_module(module_name)
    monkeypatch.setattr(
        module,
        "settings",
        SimpleNamespace(
            app_env="production",
            validate_runtime_safety=lambda: None,
            quote_worker_poll_seconds=0.25,
            export_worker_poll_seconds=0.25,
        ),
    )
    monkeypatch.setattr(
        module,
        "assert_database_schema_current",
        lambda _engine: (_ for _ in ()).throw(RuntimeError("schema is stale")),
    )
    monkeypatch.setattr(
        module,
        "run_quote_worker_once" if module_name == "quote_worker" else "run_export_worker_once",
        lambda _worker_id: pytest.fail("worker must not process a job before the schema gate"),
    )

    with pytest.raises(RuntimeError, match="schema is stale"):
        module.main()
