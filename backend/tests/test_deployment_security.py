import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "configure_vefaas_backend_env.py"


def load_configure_module():
    spec = importlib.util.spec_from_file_location("configure_vefaas_backend_env_test", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_local_config(root: Path) -> None:
    production_values = [
        "CLAMAV_HOST=127.0.0.1",
    ]
    (root / ".env.production.local").write_text("\n".join(production_values), encoding="utf-8")


def remote_key_output(required_keys: set[str] | frozenset[str], *, missing: set[str] | None = None) -> str:
    return "\n".join(sorted(required_keys - (missing or set())))


def test_vefaas_configuration_merges_without_importing_or_replacing_remote_secrets(tmp_path, monkeypatch):
    module = load_configure_module()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT_PATH), "--app-id", "app-id", "--bucket", "private-bucket", "--mode", "full"],
    )

    write_local_config(tmp_path)
    uploads: list[dict[str, str]] = []
    import_commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        if command[2] == "list":
            return SimpleNamespace(returncode=0, stdout=remote_key_output(module.FULL_MODE_REQUIRED_REMOTE_KEYS))
        env_path = Path(command[command.index("--file") + 1])
        uploads.append(module.read_env(env_path))
        import_commands.append(command)
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module.main()

    assert len(uploads) == 1
    assert not (module.FULL_MODE_REQUIRED_REMOTE_KEYS & uploads[0].keys())
    assert uploads[0]["ARTIFACT_STORAGE_BUCKET"] == "private-bucket"
    assert uploads[0]["UPLOADS_ENABLED"] == "true"
    assert uploads[0]["UPLOAD_MALWARE_SCAN_MODE"] == "clamav"
    assert uploads[0]["AI_ENABLED"] == "true"
    assert uploads[0]["CLAMAV_HOST"] == "127.0.0.1"
    assert uploads[0]["CLAMAV_READINESS_TIMEOUT_SECONDS"] == "2"
    assert "--replace" not in import_commands[0]


def test_vefaas_configuration_stops_when_a_required_remote_secret_is_missing(tmp_path, monkeypatch):
    module = load_configure_module()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT_PATH), "--app-id", "app-id", "--bucket", "private-bucket", "--mode", "full"],
    )
    write_local_config(tmp_path)

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=remote_key_output(module.FULL_MODE_REQUIRED_REMOTE_KEYS, missing={"AUTH_SECRET"}),
        ),
    )

    with pytest.raises(SystemExit, match="AUTH_SECRET"):
        module.main()


def test_vefaas_portfolio_mode_disables_uploads_scanning_and_ai(tmp_path, monkeypatch):
    module = load_configure_module()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT_PATH), "--app-id", "app-id", "--bucket", "private-bucket", "--mode", "portfolio"],
    )
    (tmp_path / ".env.production.local").write_text("", encoding="utf-8")
    uploads: list[dict[str, str]] = []

    def fake_run(command, **_kwargs):
        if command[2] == "list":
            return SimpleNamespace(returncode=0, stdout=remote_key_output(module.COMMON_REQUIRED_REMOTE_KEYS))
        env_path = Path(command[command.index("--file") + 1])
        uploads.append(module.read_env(env_path))
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module.main()

    assert len(uploads) == 1
    assert uploads[0]["UPLOADS_ENABLED"] == "false"
    assert uploads[0]["UPLOAD_MALWARE_SCAN_MODE"] == "disabled"
    assert uploads[0]["CLAMAV_HOST"] == ""
    assert uploads[0]["AI_ENABLED"] == "false"
