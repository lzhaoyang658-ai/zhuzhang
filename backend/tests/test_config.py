import pytest
from sqlalchemy.engine import make_url

from app.core.config import BACKEND_ROOT, Settings, normalize_database_url


def production_settings(**overrides) -> Settings:
    values = {
        "app_env": "production",
        "seed_demo_enabled": False,
        "auth_secret": "a" * 32,
        "auth_delivery_mode": "smtp",
        "auth_cookie_secure": True,
        "auth_allow_demo_header": False,
        "upload_malware_scan_mode": "clamav",
        "clamav_host": "clamav.internal",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_runtime_safety_accepts_secure_configuration():
    production_settings().validate_runtime_safety()


def test_production_runtime_safety_accepts_disabled_uploads_without_clamav():
    production_settings(
        uploads_enabled=False,
        upload_malware_scan_mode="disabled",
        clamav_host="",
    ).validate_runtime_safety()


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"seed_demo_enabled": True}, "SEED_DEMO_ENABLED"),
        ({"auth_delivery_mode": "development"}, "development"),
        ({"auth_secret": "short"}, "AUTH_SECRET"),
        ({"auth_secret": "replace-with-at-least-32-random-characters"}, "AUTH_SECRET"),
        ({"auth_cookie_secure": False}, "Cookie"),
        ({"auth_allow_demo_header": True}, "Header"),
        ({"upload_malware_scan_mode": "disabled"}, "ClamAV"),
        ({"clamav_host": ""}, "CLAMAV_HOST"),
        ({"clamav_port": 0}, "CLAMAV_PORT"),
        ({"clamav_port": 65_536}, "CLAMAV_PORT"),
        ({"clamav_timeout_seconds": 0}, "CLAMAV_TIMEOUT_SECONDS"),
        ({"clamav_timeout_seconds": float("nan")}, "CLAMAV_TIMEOUT_SECONDS"),
        ({"clamav_timeout_seconds": float("inf")}, "CLAMAV_TIMEOUT_SECONDS"),
        ({"clamav_readiness_timeout_seconds": 0}, "CLAMAV_READINESS_TIMEOUT_SECONDS"),
        ({"clamav_readiness_timeout_seconds": float("nan")}, "CLAMAV_READINESS_TIMEOUT_SECONDS"),
        ({"clamav_readiness_timeout_seconds": float("inf")}, "CLAMAV_READINESS_TIMEOUT_SECONDS"),
    ],
)
def test_production_runtime_safety_rejects_insecure_configuration(override, message):
    with pytest.raises(RuntimeError, match=message):
        production_settings(**override).validate_runtime_safety()


def test_demo_header_only_works_in_non_production_environments():
    assert production_settings(auth_allow_demo_header=True).demo_identity_enabled is False
    assert Settings(_env_file=None, app_env="test", auth_allow_demo_header=True).demo_identity_enabled is True


def test_relative_application_paths_are_anchored_to_backend(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite:///./data/test.db",
        upload_dir="./data/uploads",
        export_dir="local/exports",
    )

    assert make_url(settings.database_url).database == str(BACKEND_ROOT / "data/test.db")
    assert settings.upload_dir == BACKEND_ROOT / "data/uploads"
    assert settings.export_dir == BACKEND_ROOT / "local/exports"


def test_absolute_and_non_sqlite_database_urls_are_preserved(tmp_path):
    sqlite_path = tmp_path / "absolute.db"
    sqlite_url = f"sqlite:///{sqlite_path}"
    postgres_url = "postgresql+psycopg://app:secret@db.internal/app"

    assert normalize_database_url(sqlite_url) == sqlite_url
    assert normalize_database_url(postgres_url) == postgres_url
    assert normalize_database_url("sqlite:///:memory:") == "sqlite:///:memory:"
