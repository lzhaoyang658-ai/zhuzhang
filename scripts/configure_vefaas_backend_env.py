#!/usr/bin/env python3
"""Merge production settings without importing or replacing remote secret values."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMMON_REQUIRED_REMOTE_KEYS = frozenset({
    "ARTIFACT_STORAGE_ACCESS_KEY_ID",
    "ARTIFACT_STORAGE_SECRET_ACCESS_KEY",
    "AUTH_SECRET",
    "SMTP_FROM_EMAIL",
    "SMTP_HOST",
    "SMTP_PASSWORD",
    "SMTP_USERNAME",
})
FULL_MODE_REQUIRED_REMOTE_KEYS = COMMON_REQUIRED_REMOTE_KEYS | {"DASHSCOPE_API_KEY"}


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def required(source: dict[str, str], key: str) -> str:
    value = source.get(key, "").strip()
    if not value:
        raise SystemExit(f"缺少本地配置：{key}")
    return value


def remote_env_names(app_id: str) -> set[str]:
    completed = subprocess.run(
        [
            "vefaas",
            "env",
            "list",
            "--appId",
            app_id,
            "-o",
            "json",
            "--jq",
            ".data | keys[]",
            "--raw-output",
        ],
        cwd=ROOT / "backend",
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit("无法核对 veFaaS 远端配置项；请运行 vefaas auth status 后重试")
    return {line.strip() for line in completed.stdout.splitlines() if line.strip()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--bucket", required=True, help="私有对象存储桶名称")
    parser.add_argument(
        "--mode",
        choices=("portfolio", "full"),
        required=True,
        help="portfolio 关闭上传和 AI；full 开启上传并强制使用 ClamAV",
    )
    args = parser.parse_args()

    production_local = read_env(ROOT / ".env.production.local")
    full_mode = args.mode == "full"
    required_remote_keys = FULL_MODE_REQUIRED_REMOTE_KEYS if full_mode else COMMON_REQUIRED_REMOTE_KEYS
    missing_remote_keys = sorted(required_remote_keys - remote_env_names(args.app_id))
    if missing_remote_keys:
        raise SystemExit(
            "远端缺少必要 Secret/配置项："
            + ", ".join(missing_remote_keys)
            + "；请先在部署平台安全配置，不要写入仓库"
        )
    production = {
        "APP_NAME": "装修预算与增项管家",
        "APP_ENV": "production",
        "SEED_DEMO_ENABLED": "false",
        "DATABASE_URL": "sqlite:////tmp/zhuzhang/app.db",
        "UPLOAD_DIR": "/tmp/zhuzhang/uploads",
        "EXPORT_DIR": "/tmp/zhuzhang/exports",
        "SOURCE_STORAGE_BACKEND": "s3",
        "SOURCE_STORAGE_PREFIX": "project-files",
        "SOURCE_STORAGE_PRESIGN_SECONDS": "300",
        "UPLOAD_MAX_FILE_BYTES": "31457280",
        "UPLOAD_PROJECT_MAX_FILES": "200",
        "UPLOAD_PROJECT_MAX_BYTES": "1073741824",
        "UPLOAD_ZIP_MAX_ENTRIES": "2000",
        "UPLOAD_ZIP_MAX_ENTRY_BYTES": "52428800",
        "UPLOAD_ZIP_MAX_TOTAL_BYTES": "209715200",
        "UPLOAD_IMAGE_MAX_PIXELS": "50000000",
        "UPLOADS_ENABLED": "true" if full_mode else "false",
        "UPLOAD_MALWARE_SCAN_MODE": "clamav" if full_mode else "disabled",
        "CLAMAV_HOST": required(production_local, "CLAMAV_HOST") if full_mode else "",
        "CLAMAV_PORT": production_local.get("CLAMAV_PORT", "3310"),
        "CLAMAV_TIMEOUT_SECONDS": production_local.get("CLAMAV_TIMEOUT_SECONDS", "10"),
        "CLAMAV_READINESS_TIMEOUT_SECONDS": production_local.get("CLAMAV_READINESS_TIMEOUT_SECONDS", "2"),
        "ARTIFACT_STORAGE_BACKEND": "s3",
        "ARTIFACT_STORAGE_BUCKET": args.bucket,
        "ARTIFACT_STORAGE_PREFIX": "project-exports",
        "ARTIFACT_STORAGE_REGION": "cn-beijing",
        "ARTIFACT_STORAGE_ENDPOINT_URL": "https://tos-s3-cn-beijing.volces.com",
        "ARTIFACT_STORAGE_PRESIGN_SECONDS": "300",
        "ARTIFACT_STORAGE_SSE": "AES256",
        "DATABASE_BACKUP_ENABLED": "true",
        "DATABASE_BACKUP_PREFIX": "database-backups",
        "DATABASE_BACKUP_INTERVAL_SECONDS": "300",
        "MAINTENANCE_CLEANUP_ENABLED": "true",
        "MAINTENANCE_CLEANUP_INTERVAL_SECONDS": "600",
        "MAINTENANCE_STAGING_MAX_AGE_SECONDS": "86400",
        "QUOTE_EXECUTION_MODE": "embedded",
        "EXPORT_EXECUTION_MODE": "embedded",
        "HEALTH_REQUIRE_WORKERS": "false",
        "CORS_ORIGINS": "",
        "TRUSTED_PROXY_CIDRS": production_local.get("TRUSTED_PROXY_CIDRS", ""),
        "AUTH_DELIVERY_MODE": "smtp",
        "AUTH_COOKIE_SECURE": "true",
        "AUTH_ALLOW_DEMO_HEADER": "false",
        "AI_ENABLED": "true" if full_mode else "false",
        "AI_PROVIDER": "qwen",
    }

    handle, temp_path = tempfile.mkstemp(prefix="zhuzhang-vefaas-", suffix=".env")
    try:
        os.fchmod(handle, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            for key, value in production.items():
                if "\n" in value or "\r" in value:
                    raise SystemExit(f"配置值不允许换行：{key}")
                output.write(f"{key}={value}\n")
        completed = subprocess.run(
            ["vefaas", "env", "import", "--appId", args.app_id, "--file", temp_path, "--yes", "-o", "json"],
            cwd=ROOT / "backend",
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise SystemExit("veFaaS 环境变量写入失败；请运行 vefaas auth status 后重试")
        print(
            f"生产环境变量已增量写入（模式 {args.mode}，{len(production)} 项，"
            "远端 Secret 值未导入脚本、未覆盖）"
        )
    finally:
        Path(temp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
