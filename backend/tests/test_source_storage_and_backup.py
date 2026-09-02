import io
import hashlib
import json
import sqlite3

import pytest

from app.core.config import get_settings
from app.services.database_backup import (
    backup_sqlite_database,
    database_backup_status,
    restore_sqlite_backup,
    verify_remote_sqlite_backup,
)
from app.services.source_storage import LocalSourceStorage, S3SourceStorage


class FakeBody(io.BytesIO):
    pass


class FakeS3Client:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.metadata: dict[tuple[str, str], dict[str, str]] = {}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[(Bucket, Key)] = bytes(Body)
        self.metadata[(Bucket, Key)] = kwargs.get("Metadata", {})
        return {"VersionId": "version-1"}

    def head_object(self, Bucket, Key):
        content = self.objects[(Bucket, Key)]
        return {"ContentLength": len(content), "Metadata": self.metadata.get((Bucket, Key), {})}

    def delete_object(self, Bucket, Key, VersionId=None):
        del VersionId
        self.objects.pop((Bucket, Key), None)
        self.metadata.pop((Bucket, Key), None)

    def get_object(self, Bucket, Key):
        return {"Body": FakeBody(self.objects[(Bucket, Key)])}

    def upload_fileobj(self, stream, bucket, key, ExtraArgs=None):
        del ExtraArgs
        self.objects[(bucket, key)] = stream.read()

    def download_fileobj(self, bucket, key, stream):
        stream.write(self.objects[(bucket, key)])

    def generate_presigned_url(self, _method, Params, ExpiresIn):
        return f"https://private.example/{Params['Key']}?ttl={ExpiresIn}"

    def list_objects_v2(self, Bucket, Prefix, **_kwargs):
        contents = [{"Key": key} for bucket, key in self.objects if bucket == Bucket and key.startswith(Prefix)]
        return {"Contents": contents, "IsTruncated": False}

    def delete_objects(self, Bucket, Delete):
        for item in Delete["Objects"]:
            self.objects.pop((Bucket, item["Key"]), None)


def test_local_source_storage_rejects_traversal_and_round_trips(tmp_path):
    storage = LocalSourceStorage(tmp_path / "uploads")
    receipt = storage.store_bytes("project/quotes/file.csv", b"quote", "text/csv")
    assert storage.read_bytes("project/quotes/file.csv") == b"quote"
    assert storage.verify("project/quotes/file.csv", 5, hashlib.sha256(b"quote").hexdigest())
    assert receipt.version_id is None
    assert storage.delete_project("project") == 1
    with pytest.raises(ValueError, match="非法原始文件对象键"):
        storage.store_bytes("../outside", b"bad", "text/plain")


def test_s3_source_storage_round_trips_presigns_and_deletes(monkeypatch, tmp_path):
    fake = FakeS3Client()
    settings = get_settings()
    monkeypatch.setattr(settings, "artifact_storage_bucket", "private-files")
    monkeypatch.setattr(settings, "source_storage_prefix", "production/files")
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "cache")
    monkeypatch.setattr("app.services.source_storage.create_private_s3_client", lambda: fake)

    storage = S3SourceStorage()
    digest = hashlib.sha256(b"source").hexdigest()
    receipt = storage.store_bytes(
        "project/quotes/source.csv",
        b"source",
        "text/csv",
        {"sha256": digest},
    )
    assert storage.read_bytes("project/quotes/source.csv") == b"source"
    assert storage.verify("project/quotes/source.csv", 6, digest)
    assert receipt.version_id == "version-1"
    assert storage.ensure_local("project/quotes/source.csv").read_bytes() == b"source"
    assert "ttl=300" in storage.download_url("project/quotes/source.csv", "报价.csv", "text/csv")
    assert storage.delete("project/quotes/source.csv", receipt.version_id)
    assert ("private-files", "production/files/project/quotes/source.csv") not in fake.objects


def test_sqlite_backup_and_restore_use_consistent_snapshot(monkeypatch, tmp_path):
    fake = FakeS3Client()
    database_path = tmp_path / "app.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("create table sample (value text not null)")
        connection.execute("insert into sample values ('persisted')")

    settings = get_settings()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{database_path}")
    monkeypatch.setattr(settings, "database_backup_enabled", True)
    monkeypatch.setattr(settings, "database_backup_prefix", "backups")
    monkeypatch.setattr(settings, "artifact_storage_bucket", "private-files")
    monkeypatch.setattr("app.services.database_backup.create_private_s3_client", lambda: fake)

    assert backup_sqlite_database()
    manifest = json.loads(fake.objects[("private-files", "backups/latest.json")])
    assert manifest["version"] == 1
    assert manifest["object_key"].startswith("backups/snapshots/")
    assert manifest["size_bytes"] > 0 and len(manifest["sha256"]) == 64
    verified = verify_remote_sqlite_backup()
    assert verified["manifest_version"] == 1
    assert verified["sha256"] == manifest["sha256"]
    assert database_backup_status()["status"] == "healthy"
    database_path.unlink()
    assert restore_sqlite_backup()
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("select value from sample").fetchone()[0] == "persisted"


def test_sqlite_restore_rejects_tampered_snapshot_without_replacing_database(monkeypatch, tmp_path):
    fake = FakeS3Client()
    database_path = tmp_path / "app.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("create table sample (value text not null)")
        connection.execute("insert into sample values ('trusted')")

    settings = get_settings()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{database_path}")
    monkeypatch.setattr(settings, "database_backup_enabled", True)
    monkeypatch.setattr(settings, "database_backup_prefix", "backups-tamper")
    monkeypatch.setattr(settings, "artifact_storage_bucket", "private-files")
    monkeypatch.setattr("app.services.database_backup.create_private_s3_client", lambda: fake)

    assert backup_sqlite_database()
    manifest = json.loads(fake.objects[("private-files", "backups-tamper/latest.json")])
    fake.objects[("private-files", manifest["object_key"])] = b"not-a-database"
    database_path.unlink()

    with pytest.raises(RuntimeError, match="完整性校验失败"):
        restore_sqlite_backup()
    assert not database_path.exists()
