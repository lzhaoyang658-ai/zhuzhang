from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping
from urllib.parse import quote

from app.core.config import get_settings
from app.services.s3_client import create_private_s3_client
from app.services.s3_delete import delete_s3_prefix


settings = get_settings()


@dataclass(frozen=True)
class StoredSourceObject:
    object_key: str
    version_id: str | None = None


def _safe_key(object_key: str) -> str:
    normalized = PurePosixPath(object_key)
    if not object_key or normalized.is_absolute() or ".." in normalized.parts:
        raise ValueError("非法原始文件对象键")
    return str(normalized)


class LocalSourceStorage:
    backend = "local"

    def __init__(self, root: Path):
        self.root = root

    def _path(self, object_key: str) -> Path:
        safe_key = _safe_key(object_key)
        root = self.root.resolve()
        path = (self.root / safe_key).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("非法原始文件对象键") from exc
        return path

    def store_bytes(
        self,
        object_key: str,
        content: bytes,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StoredSourceObject:
        del content_type, metadata
        target = self._path(object_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return StoredSourceObject(object_key=object_key)

    def verify(self, object_key: str, expected_size: int, expected_sha256: str) -> bool:
        path = self.ensure_local(object_key)
        if not path or path.stat().st_size != expected_size:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == expected_sha256

    def delete(self, object_key: str, version_id: str | None = None) -> bool:
        del version_id
        target = self._path(object_key)
        if not target.exists() and not target.is_symlink():
            return False
        target.unlink()
        parent = target.parent
        root = self.root.resolve()
        while parent != root and parent.is_dir():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
        return True

    def ensure_local(self, object_key: str) -> Path | None:
        path = self._path(object_key)
        return path if path.is_file() else None

    def read_bytes(self, object_key: str) -> bytes:
        path = self.ensure_local(object_key)
        if not path:
            raise FileNotFoundError(object_key)
        return path.read_bytes()

    def download_url(self, object_key: str, filename: str, content_type: str) -> str | None:
        del object_key, filename, content_type
        return None

    def delete_project(self, project_id: str) -> int:
        project_dir = self._path(project_id)
        if not project_dir.is_dir():
            return 0
        count = 0
        for item in sorted(project_dir.rglob("*"), key=lambda path: len(path.parts), reverse=True):
            if item.is_file() or item.is_symlink():
                item.unlink()
                count += 1
            elif item.is_dir():
                item.rmdir()
        project_dir.rmdir()
        return count


class S3SourceStorage:
    backend = "s3"

    def __init__(self):
        if not settings.artifact_storage_bucket.strip():
            raise RuntimeError("S3 原始文件存储缺少 ARTIFACT_STORAGE_BUCKET")
        self.client = create_private_s3_client()
        self.bucket = settings.artifact_storage_bucket
        self.prefix = settings.source_storage_prefix.strip("/")

    def _key(self, object_key: str) -> str:
        safe_key = _safe_key(object_key)
        return f"{self.prefix}/{safe_key}" if self.prefix else safe_key

    def store_bytes(
        self,
        object_key: str,
        content: bytes,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> StoredSourceObject:
        extra: dict[str, object] = {"ContentType": content_type or "application/octet-stream"}
        if settings.artifact_storage_sse:
            extra["ServerSideEncryption"] = settings.artifact_storage_sse
        if metadata:
            extra["Metadata"] = {
                str(key).lower(): str(value)
                for key, value in metadata.items()
                if value is not None
            }
        response = self.client.put_object(Bucket=self.bucket, Key=self._key(object_key), Body=content, **extra) or {}
        return StoredSourceObject(object_key=object_key, version_id=response.get("VersionId"))

    def verify(self, object_key: str, expected_size: int, expected_sha256: str) -> bool:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=self._key(object_key))
        except Exception:
            return False
        metadata = {str(key).lower(): str(value) for key, value in (response.get("Metadata") or {}).items()}
        return (
            int(response.get("ContentLength", -1)) == expected_size
            and metadata.get("sha256") == expected_sha256
        )

    def delete(self, object_key: str, version_id: str | None = None) -> bool:
        params: dict[str, str] = {"Bucket": self.bucket, "Key": self._key(object_key)}
        if version_id:
            params["VersionId"] = version_id
        self.client.delete_object(**params)
        return True

    def ensure_local(self, object_key: str) -> Path | None:
        safe_key = _safe_key(object_key)
        target = (settings.upload_dir / safe_key).resolve()
        root = settings.upload_dir.resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("非法原始文件对象键") from exc
        if target.is_file():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("wb") as stream:
                self.client.download_fileobj(self.bucket, self._key(object_key), stream)
        except Exception:
            target.unlink(missing_ok=True)
            return None
        return target

    def read_bytes(self, object_key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=self._key(object_key))
        return response["Body"].read()

    def download_url(self, object_key: str, filename: str, content_type: str) -> str | None:
        disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": self._key(object_key),
                "ResponseContentType": content_type or "application/octet-stream",
                "ResponseContentDisposition": disposition,
            },
            ExpiresIn=max(60, min(settings.source_storage_presign_seconds, 3600)),
        )

    def delete_project(self, project_id: str) -> int:
        project_prefix = _safe_key(project_id).rstrip("/") + "/"
        prefix = f"{self.prefix}/{project_prefix}" if self.prefix else project_prefix
        return delete_s3_prefix(self.client, self.bucket, prefix)


def get_source_storage():
    if settings.source_storage_backend == "local":
        return LocalSourceStorage(settings.upload_dir)
    if settings.source_storage_backend == "s3":
        return S3SourceStorage()
    raise RuntimeError(f"尚未配置原始文件存储后端：{settings.source_storage_backend}")
