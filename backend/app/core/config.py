import ipaddress
import math
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[2]


def resolve_backend_path(value: str | Path) -> Path:
    """Resolve application-owned relative paths independently from process CWD."""
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (BACKEND_ROOT / path).resolve()


def normalize_database_url(value: str) -> str:
    """Anchor relative SQLite files to ``backend/`` while preserving other URLs."""
    url = make_url(value)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return value
    # SQLite URI filenames (for example ``file:memory?mode=memory``) have their
    # own resolution rules and must not be rewritten as ordinary filesystem paths.
    if url.database.startswith("file:"):
        return value
    path = resolve_backend_path(url.database)
    return url.set(database=str(path)).render_as_string(hide_password=False)


def sqlite_database_path_from_url(value: str) -> Path | None:
    """Return the local file behind a regular SQLite URL, if it has one."""
    url = make_url(value)
    if (
        not url.drivername.startswith("sqlite")
        or not url.database
        or url.database == ":memory:"
        or url.database.startswith("file:")
    ):
        return None
    return resolve_backend_path(url.database)


class Settings(BaseSettings):
    app_name: str = "装修预算与增项管家"
    app_env: Literal["development", "test", "production"] = "production"
    seed_demo_enabled: bool = False
    database_url: str = "sqlite:///./data/app.db"
    upload_dir: Path = Path("./data/uploads")
    export_dir: Path = Path("./data/exports")
    source_storage_backend: str = "local"
    source_storage_prefix: str = "project-files"
    source_storage_presign_seconds: int = 300
    uploads_enabled: bool = True
    upload_max_file_bytes: int = 30 * 1024 * 1024
    upload_project_max_files: int = 200
    upload_project_max_bytes: int = 1024 * 1024 * 1024
    upload_zip_max_entries: int = 2000
    upload_zip_max_entry_bytes: int = 50 * 1024 * 1024
    upload_zip_max_total_bytes: int = 200 * 1024 * 1024
    upload_image_max_pixels: int = 50_000_000
    upload_malware_scan_mode: Literal["disabled", "clamav"] = "disabled"
    clamav_host: str = ""
    clamav_port: int = 3310
    clamav_timeout_seconds: float = 10.0
    clamav_readiness_timeout_seconds: float = 2.0
    artifact_storage_backend: str = "local"
    artifact_storage_bucket: str = ""
    artifact_storage_prefix: str = "project-exports"
    artifact_storage_region: str = ""
    artifact_storage_endpoint_url: str = ""
    artifact_storage_access_key_id: str = ""
    artifact_storage_secret_access_key: str = ""
    artifact_storage_presign_seconds: int = 300
    artifact_storage_sse: str = "AES256"
    export_execution_mode: str = "embedded"
    export_part_size_mb: int = 100
    export_job_lease_seconds: int = 3600
    export_job_max_attempts: int = 3
    export_retry_base_seconds: int = 30
    export_worker_poll_seconds: float = 2.0
    quote_execution_mode: str = "embedded"
    quote_job_lease_seconds: int = 3600
    quote_job_max_attempts: int = 3
    quote_retry_base_seconds: int = 30
    quote_worker_poll_seconds: float = 2.0
    worker_heartbeat_seconds: float = 5.0
    worker_heartbeat_stale_seconds: int = 20
    worker_queues: str = "quote,export"
    worker_require_postgresql: bool = False
    worker_failure_backoff_seconds: float = 5.0
    worker_shutdown_seconds: int = 10
    health_require_workers: bool = False
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_pool_recycle_seconds: int = 300
    database_connect_timeout_seconds: int = 10
    maintenance_mode: bool = False
    database_backup_enabled: bool = False
    database_backup_prefix: str = "database-backups"
    database_backup_interval_seconds: int = 300
    database_backup_max_age_seconds: int = 900
    database_backup_retention_count: int = 24
    database_backup_require_ready: bool = False
    maintenance_cleanup_enabled: bool = True
    maintenance_cleanup_interval_seconds: int = 600
    maintenance_staging_max_age_seconds: int = 86400
    cors_origins: str = "http://localhost:3001"
    trusted_proxy_cidrs: str = ""

    auth_secret: str = "local-development-only-change-before-production"
    auth_cookie_name: str = "zhuzhang_session"
    auth_csrf_cookie_name: str = "zhuzhang_csrf"
    auth_session_days: int = 30
    auth_code_minutes: int = 10
    auth_recent_minutes: int = 15
    auth_delivery_mode: str = "development"
    auth_cookie_secure: bool = False
    auth_allow_demo_header: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_use_ssl: bool = True

    ai_enabled: bool = False
    ai_provider: str = "qwen"
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_primary_model: str = "qwen3.6-flash-2026-04-16"
    qwen_fallback_model: str = "qwen3.7-plus-2026-05-26"
    qwen_ocr_model: str = "qwen3.5-ocr"
    ai_request_timeout_seconds: float = 90.0
    ai_max_pages: int = 20
    ai_max_retries: int = 2

    model_config = SettingsConfigDict(
        env_file=(REPOSITORY_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def normalize_local_paths(self) -> "Settings":
        self.database_url = normalize_database_url(self.database_url)
        self.upload_dir = resolve_backend_path(self.upload_dir)
        self.export_dir = resolve_backend_path(self.export_dir)
        return self

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def trusted_proxy_networks(self) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
        return tuple(
            ipaddress.ip_network(value.strip(), strict=False)
            for value in self.trusted_proxy_cidrs.split(",")
            if value.strip()
        )

    @property
    def ai_ready(self) -> bool:
        return self.ai_enabled and self.ai_provider == "qwen" and bool(self.dashscope_api_key.strip())

    @property
    def is_postgresql(self) -> bool:
        return make_url(self.database_url).drivername.startswith("postgresql")

    @property
    def is_sqlite(self) -> bool:
        return make_url(self.database_url).drivername.startswith("sqlite")

    @property
    def demo_identity_enabled(self) -> bool:
        return self.app_env in {"development", "test"} and self.auth_allow_demo_header

    def validate_runtime_safety(self) -> None:
        errors: list[str] = []
        try:
            self.trusted_proxy_networks
        except ValueError:
            errors.append("TRUSTED_PROXY_CIDRS 必须是逗号分隔的有效 CIDR")
        upload_limits = {
            "UPLOAD_MAX_FILE_BYTES": self.upload_max_file_bytes,
            "UPLOAD_PROJECT_MAX_FILES": self.upload_project_max_files,
            "UPLOAD_PROJECT_MAX_BYTES": self.upload_project_max_bytes,
            "UPLOAD_ZIP_MAX_ENTRIES": self.upload_zip_max_entries,
            "UPLOAD_ZIP_MAX_ENTRY_BYTES": self.upload_zip_max_entry_bytes,
            "UPLOAD_ZIP_MAX_TOTAL_BYTES": self.upload_zip_max_total_bytes,
            "UPLOAD_IMAGE_MAX_PIXELS": self.upload_image_max_pixels,
        }
        for name, value in upload_limits.items():
            if value <= 0:
                errors.append(f"{name} 必须大于 0")
        if self.upload_max_file_bytes > self.upload_project_max_bytes:
            errors.append("UPLOAD_MAX_FILE_BYTES 不能高于项目存储配额")
        if not 1 <= self.clamav_port <= 65_535:
            errors.append("CLAMAV_PORT 必须在 1 至 65535 之间")
        if not math.isfinite(self.clamav_timeout_seconds) or self.clamav_timeout_seconds <= 0:
            errors.append("CLAMAV_TIMEOUT_SECONDS 必须大于 0")
        if not math.isfinite(self.clamav_readiness_timeout_seconds) or self.clamav_readiness_timeout_seconds <= 0:
            errors.append("CLAMAV_READINESS_TIMEOUT_SECONDS 必须大于 0")
        if self.seed_demo_enabled and self.app_env != "development":
            errors.append("SEED_DEMO_ENABLED 只能在 APP_ENV=development 时启用")
        if self.app_env == "production":
            if self.auth_delivery_mode == "development":
                errors.append("生产环境不能使用 development 验证码投递")
            if (
                self.auth_secret.startswith(("local-development-", "replace-with-"))
                or len(self.auth_secret) < 32
            ):
                errors.append("生产环境必须配置至少 32 位的独立 AUTH_SECRET")
            if not self.auth_cookie_secure:
                errors.append("生产环境必须启用安全 Cookie")
            if self.auth_allow_demo_header:
                errors.append("生产环境必须关闭演示身份 Header")
            if self.uploads_enabled:
                if self.upload_malware_scan_mode != "clamav":
                    errors.append("生产环境开启上传时必须启用 ClamAV 恶意文件扫描")
                if not self.clamav_host.strip():
                    errors.append("生产环境开启上传时必须配置 CLAMAV_HOST")
        if errors:
            raise RuntimeError("；".join(errors))


@lru_cache
def get_settings() -> Settings:
    return Settings()
