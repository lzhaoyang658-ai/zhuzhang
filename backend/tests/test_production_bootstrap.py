from types import SimpleNamespace

import production_bootstrap


def test_backup_restore_failure_does_not_block_api_start(monkeypatch):
    started: dict[str, object] = {}

    monkeypatch.setattr(
        production_bootstrap,
        "get_settings",
        lambda: SimpleNamespace(
            database_url="sqlite:///ignored.db",
            trusted_proxy_cidrs="",
            validate_runtime_safety=lambda: None,
        ),
    )
    monkeypatch.setattr(production_bootstrap, "sqlite_database_path", lambda: None)
    monkeypatch.setattr(
        production_bootstrap,
        "restore_sqlite_backup",
        lambda: (_ for _ in ()).throw(RuntimeError("temporary storage failure")),
    )
    monkeypatch.setattr(production_bootstrap.command, "upgrade", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        production_bootstrap.uvicorn,
        "run",
        lambda app, **kwargs: started.update({"app": app, **kwargs}),
    )

    production_bootstrap.main()

    assert started["app"] == "app.main:app"
    assert started["host"] == "0.0.0.0"
    assert started["port"] == 8000
    assert started["proxy_headers"] is False
    assert started["forwarded_allow_ips"] == ""
