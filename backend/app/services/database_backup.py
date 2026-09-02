from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any

from app.core.config import get_settings, sqlite_database_path_from_url
from app.services.s3_client import create_private_s3_client
from app.services.s3_delete import (delete_s3_keys_permanently,
                                    prune_s3_key_noncurrent_versions)


settings = get_settings()
logger = logging.getLogger(__name__)
_stop_event: Event | None = None
_thread: Thread | None = None
_status_lock = Lock()
_last_success_at: datetime | None = None
_last_failure_at: datetime | None = None
_last_error: str | None = None
_last_size_bytes: int | None = None
_last_sha256: str | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sqlite_database_path() -> Path | None:
    return sqlite_database_path_from_url(settings.database_url)


def _backup_key() -> str:
    prefix = settings.database_backup_prefix.strip("/")
    return f"{prefix}/latest.sqlite3" if prefix else "latest.sqlite3"


def _manifest_key() -> str:
    prefix = settings.database_backup_prefix.strip("/")
    return f"{prefix}/latest.json" if prefix else "latest.json"


def _snapshot_prefix() -> str:
    prefix = settings.database_backup_prefix.strip("/")
    return f"{prefix}/snapshots/" if prefix else "snapshots/"


def _snapshot_key(created_at: datetime, sha256: str) -> str:
    timestamp = created_at.strftime("%Y%m%dT%H%M%S%fZ")
    return f"{_snapshot_prefix()}{timestamp}-{sha256[:12]}.sqlite3"


def _error_code(exc: Exception) -> str | None:
    return getattr(exc, "response", {}).get("Error", {}).get("Code")


def _is_missing(exc: Exception) -> bool:
    return isinstance(exc, KeyError) or _error_code(exc) in {"404", "NoSuchKey", "NotFound", "NoSuchBucket"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_sqlite(path: Path) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("SQLite 备份文件为空")
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise RuntimeError("SQLite 备份完整性校验失败") from exc
    if not result or result[0] != "ok":
        raise RuntimeError("SQLite 备份完整性校验失败")


def _record_success(*, created_at: datetime, size_bytes: int, sha256: str) -> None:
    global _last_success_at, _last_failure_at, _last_error, _last_size_bytes, _last_sha256
    with _status_lock:
        _last_success_at = created_at
        _last_failure_at = None
        _last_error = None
        _last_size_bytes = size_bytes
        _last_sha256 = sha256


def _record_failure(exc: Exception) -> None:
    global _last_failure_at, _last_error
    with _status_lock:
        _last_failure_at = _utc_now()
        _last_error = exc.__class__.__name__


def database_backup_status() -> dict[str, Any]:
    database_path = sqlite_database_path()
    if not settings.database_backup_enabled:
        return {"enabled": False, "status": "disabled"}
    if not database_path:
        return {"enabled": True, "status": "not_applicable"}
    with _status_lock:
        success_at = _last_success_at
        failure_at = _last_failure_at
        error = _last_error
        size_bytes = _last_size_bytes
        sha256 = _last_sha256
    now = _utc_now()
    age_seconds = max(0, int((now - success_at).total_seconds())) if success_at else None
    stale = age_seconds is not None and age_seconds > max(30, settings.database_backup_max_age_seconds)
    failed_after_success = bool(failure_at and (not success_at or failure_at > success_at))
    status = "failed" if failed_after_success else "stale" if stale else "healthy" if success_at else "pending"
    return {
        "enabled": True,
        "status": status,
        "last_success_at": success_at.isoformat() if success_at else None,
        "age_seconds": age_seconds,
        "max_age_seconds": max(30, settings.database_backup_max_age_seconds),
        "size_bytes": size_bytes,
        "checksum": sha256[:12] if sha256 else None,
        "last_error_type": error,
    }


def _load_manifest(client: Any) -> dict[str, Any] | None:
    try:
        response = client.get_object(Bucket=settings.artifact_storage_bucket, Key=_manifest_key())
        raw = response["Body"].read()
    except Exception as exc:
        if _is_missing(exc):
            return None
        raise
    try:
        manifest = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("数据库备份清单格式无效") from exc
    required = {"version", "created_at", "object_key", "size_bytes", "sha256"}
    if manifest.get("version") != 1 or not required.issubset(manifest):
        raise RuntimeError("数据库备份清单格式无效")
    return manifest


def _download_and_verify(client: Any, target: Path) -> dict[str, Any]:
    manifest = _load_manifest(client)
    object_key = manifest["object_key"] if manifest else _backup_key()
    with target.open("wb") as stream:
        client.download_fileobj(settings.artifact_storage_bucket, object_key, stream)
    _verify_sqlite(target)
    actual_size = target.stat().st_size
    actual_sha256 = _sha256(target)
    if manifest:
        if actual_size != int(manifest["size_bytes"]) or actual_sha256 != manifest["sha256"]:
            raise RuntimeError("数据库备份校验和不匹配")
        created_at = datetime.fromisoformat(str(manifest["created_at"]).replace("Z", "+00:00"))
    else:
        created_at = _utc_now()
    _record_success(created_at=created_at, size_bytes=actual_size, sha256=actual_sha256)
    return {
        "created_at": created_at.isoformat(),
        "size_bytes": actual_size,
        "sha256": actual_sha256,
        "manifest_version": manifest["version"] if manifest else 0,
    }


def verify_remote_sqlite_backup() -> dict[str, Any]:
    if not settings.database_backup_enabled or not sqlite_database_path():
        raise RuntimeError("SQLite 云备份未启用")
    if not settings.artifact_storage_bucket.strip():
        raise RuntimeError("数据库云备份缺少 ARTIFACT_STORAGE_BUCKET")
    file_descriptor, temp_name = tempfile.mkstemp(prefix="zhuzhang-db-verify-", suffix=".sqlite3")
    os.close(file_descriptor)
    temp_path = Path(temp_name)
    try:
        return _download_and_verify(create_private_s3_client(), temp_path)
    except Exception as exc:
        _record_failure(exc)
        raise
    finally:
        temp_path.unlink(missing_ok=True)


def restore_sqlite_backup() -> bool:
    database_path = sqlite_database_path()
    if not settings.database_backup_enabled or not database_path:
        return False
    if database_path.exists():
        try:
            _verify_sqlite(database_path)
            return False
        except RuntimeError:
            pass
    if not settings.artifact_storage_bucket.strip():
        raise RuntimeError("数据库云备份缺少 ARTIFACT_STORAGE_BUCKET")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = database_path.with_suffix(database_path.suffix + ".restore")
    client = create_private_s3_client()
    try:
        _download_and_verify(client, temp_path)
    except Exception as exc:
        temp_path.unlink(missing_ok=True)
        if _is_missing(exc):
            return False
        _record_failure(exc)
        raise
    os.replace(temp_path, database_path)
    return True


def _prune_snapshots(client: Any) -> None:
    keep = max(1, settings.database_backup_retention_count)
    try:
        keys: list[str] = []
        continuation: str | None = None
        while True:
            request: dict[str, Any] = {"Bucket": settings.artifact_storage_bucket, "Prefix": _snapshot_prefix()}
            if continuation:
                request["ContinuationToken"] = continuation
            response = client.list_objects_v2(**request)
            keys.extend(item["Key"] for item in response.get("Contents", []))
            if not response.get("IsTruncated"):
                break
            continuation = response.get("NextContinuationToken")
            if not continuation:
                raise RuntimeError("数据库备份对象列表分页缺少 NextContinuationToken")
        keys.sort(reverse=True)
        stale = keys[keep:]
        if stale:
            delete_s3_keys_permanently(client, settings.artifact_storage_bucket, stale)
        prune_s3_key_noncurrent_versions(client, settings.artifact_storage_bucket, _backup_key())
        prune_s3_key_noncurrent_versions(client, settings.artifact_storage_bucket, _manifest_key())
    except Exception:
        # Retention cleanup must not turn a successfully uploaded recovery point
        # into a failed backup.
        logger.exception("Database backup retention cleanup failed")


def backup_sqlite_database() -> bool:
    database_path = sqlite_database_path()
    if not settings.database_backup_enabled or not database_path or not database_path.is_file():
        return False
    if not settings.artifact_storage_bucket.strip():
        raise RuntimeError("数据库云备份缺少 ARTIFACT_STORAGE_BUCKET")

    file_descriptor, temp_name = tempfile.mkstemp(prefix="zhuzhang-db-", suffix=".sqlite3")
    os.close(file_descriptor)
    temp_path = Path(temp_name)
    try:
        with sqlite3.connect(database_path) as source, sqlite3.connect(temp_path) as target:
            source.backup(target)
        _verify_sqlite(temp_path)
        created_at = _utc_now()
        size_bytes = temp_path.stat().st_size
        sha256 = _sha256(temp_path)
        snapshot_key = _snapshot_key(created_at, sha256)
        metadata: dict[str, Any] = {
            "ContentType": "application/vnd.sqlite3",
            "Metadata": {"backed-up-at": created_at.isoformat(), "sha256": sha256},
        }
        if settings.artifact_storage_sse:
            metadata["ServerSideEncryption"] = settings.artifact_storage_sse
        client = create_private_s3_client()
        for object_key in (snapshot_key, _backup_key()):
            with temp_path.open("rb") as stream:
                client.upload_fileobj(
                    stream,
                    settings.artifact_storage_bucket,
                    object_key,
                    ExtraArgs=metadata,
                )
        manifest = {
            "version": 1,
            "created_at": created_at.isoformat(),
            "object_key": snapshot_key,
            "size_bytes": size_bytes,
            "sha256": sha256,
        }
        manifest_args: dict[str, Any] = {"ContentType": "application/json"}
        if settings.artifact_storage_sse:
            manifest_args["ServerSideEncryption"] = settings.artifact_storage_sse
        client.put_object(
            Bucket=settings.artifact_storage_bucket,
            Key=_manifest_key(),
            Body=json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            **manifest_args,
        )
        _record_success(created_at=created_at, size_bytes=size_bytes, sha256=sha256)
        _prune_snapshots(client)
        return True
    except Exception as exc:
        _record_failure(exc)
        raise
    finally:
        temp_path.unlink(missing_ok=True)


def start_database_backup_scheduler() -> bool:
    global _stop_event, _thread
    if not settings.database_backup_enabled or not sqlite_database_path() or (_thread and _thread.is_alive()):
        return False
    _stop_event = Event()

    def run() -> None:
        while _stop_event and not _stop_event.is_set():
            try:
                backup_sqlite_database()
            except Exception:
                pass
            if _stop_event.wait(max(30, settings.database_backup_interval_seconds)):
                break

    _thread = Thread(target=run, name="database-backup", daemon=True)
    _thread.start()
    return True


def stop_database_backup_scheduler() -> None:
    global _stop_event, _thread
    if _stop_event:
        _stop_event.set()
    if _thread:
        _thread.join(timeout=3)
    if settings.database_backup_enabled:
        try:
            backup_sqlite_database()
        except Exception:
            pass
    _stop_event = None
    _thread = None
