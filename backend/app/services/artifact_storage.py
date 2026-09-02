from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import quote

from app.core.config import get_settings
from app.services.s3_client import create_private_s3_client
from app.services.s3_delete import delete_s3_prefix


settings = get_settings()


@dataclass(frozen=True)
class StoredArtifact:
    object_key: str
    size_bytes: int
    sha256: str
    backend: str


class PrivateArtifactStorage(Protocol):
    backend: str

    def store_file(self, source: Path, object_key: str) -> StoredArtifact: ...
    def local_path(self, object_key: str) -> Path | None: ...
    def delete(self, object_key: str) -> bool: ...
    def delete_project(self, project_id: str) -> int: ...
    def verify(self, object_key: str, expected_sha256: str | None, expected_size: int | None = None) -> bool: ...
    def download_url(self, object_key: str, filename: str) -> str | None: ...


def _validate_object_key(object_key: str) -> str:
    normalized = PurePosixPath(object_key)
    if not object_key or normalized.is_absolute() or ".." in normalized.parts:
        raise ValueError("非法档案对象键")
    return str(normalized)


def _file_digest(source: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


class LocalPrivateArtifactStorage:
    backend = "local"

    def __init__(self, root: Path):
        self.root = root

    def _path(self, object_key: str) -> Path:
        safe_key = _validate_object_key(object_key)
        root = self.root.resolve()
        path = (self.root / safe_key).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("非法档案对象键") from exc
        return path

    def store_file(self, source: Path, object_key: str) -> StoredArtifact:
        target = self._path(object_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        sha256, size = _file_digest(source)
        if source.resolve() != target:
            shutil.move(str(source), str(target))
        return StoredArtifact(object_key=object_key, size_bytes=size, sha256=sha256, backend=self.backend)

    def local_path(self, object_key: str) -> Path | None:
        path = self._path(object_key)
        return path if path.is_file() else None

    def delete(self, object_key: str) -> bool:
        path = self.local_path(object_key)
        if not path:
            return False
        path.unlink()
        return True

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

    def verify(self, object_key: str, expected_sha256: str | None, expected_size: int | None = None) -> bool:
        path = self.local_path(object_key)
        if not path:
            return False
        if expected_size is not None and path.stat().st_size != expected_size:
            return False
        if not expected_sha256:
            return True
        sha256, _ = _file_digest(path)
        return sha256 == expected_sha256

    def download_url(self, object_key: str, filename: str) -> str | None:
        return None


class S3PrivateArtifactStorage:
    backend = "s3"

    def __init__(self):
        if not settings.artifact_storage_bucket.strip():
            raise RuntimeError("S3 档案存储缺少 ARTIFACT_STORAGE_BUCKET")
        self.client = create_private_s3_client()
        self.bucket = settings.artifact_storage_bucket
        self.prefix = settings.artifact_storage_prefix.strip("/")

    def _key(self, object_key: str) -> str:
        safe_key = _validate_object_key(object_key)
        return f"{self.prefix}/{safe_key}" if self.prefix else safe_key

    def store_file(self, source: Path, object_key: str) -> StoredArtifact:
        sha256, size = _file_digest(source)
        extra_args: dict[str, object] = {
            "ContentType": "application/zip",
            "Metadata": {"sha256": sha256},
        }
        if settings.artifact_storage_sse:
            extra_args["ServerSideEncryption"] = settings.artifact_storage_sse
        with source.open("rb") as stream:
            self.client.upload_fileobj(stream, self.bucket, self._key(object_key), ExtraArgs=extra_args)
        source.unlink()
        return StoredArtifact(object_key=object_key, size_bytes=size, sha256=sha256, backend=self.backend)

    def local_path(self, object_key: str) -> Path | None:
        return None

    def delete(self, object_key: str) -> bool:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(object_key))
        return True

    def delete_project(self, project_id: str) -> int:
        project_prefix = _validate_object_key(project_id).rstrip("/") + "/"
        prefix = f"{self.prefix}/{project_prefix}" if self.prefix else project_prefix
        return delete_s3_prefix(self.client, self.bucket, prefix)

    def verify(self, object_key: str, expected_sha256: str | None, expected_size: int | None = None) -> bool:
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=self._key(object_key))
        except Exception:
            return False
        if expected_size is not None and head.get("ContentLength") != expected_size:
            return False
        metadata = {str(key).lower(): value for key, value in head.get("Metadata", {}).items()}
        return not expected_sha256 or metadata.get("sha256") == expected_sha256

    def download_url(self, object_key: str, filename: str) -> str | None:
        disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": self._key(object_key),
                "ResponseContentType": "application/zip",
                "ResponseContentDisposition": disposition,
            },
            ExpiresIn=max(60, min(settings.artifact_storage_presign_seconds, 3600)),
        )


def get_artifact_storage(backend: str | None = None) -> PrivateArtifactStorage:
    selected = backend or settings.artifact_storage_backend
    if selected == "local":
        return LocalPrivateArtifactStorage(settings.export_dir)
    if selected == "s3":
        return S3PrivateArtifactStorage()
    raise RuntimeError(f"尚未配置档案存储后端：{selected}")
