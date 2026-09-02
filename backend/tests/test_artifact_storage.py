from types import SimpleNamespace
import sys
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.models import ProjectExportJob
from app.services.artifact_storage import LocalPrivateArtifactStorage, S3PrivateArtifactStorage
from app.services.export_jobs import export_job_payload


class FakeS3Client:
    def __init__(self):
        self.objects = {}

    def upload_fileobj(self, stream, bucket, key, ExtraArgs):
        self.objects[(bucket, key)] = {"body": stream.read(), "extra": ExtraArgs}

    def head_object(self, Bucket, Key):
        item = self.objects[(Bucket, Key)]
        return {"ContentLength": len(item["body"]), "Metadata": item["extra"]["Metadata"]}

    def generate_presigned_url(self, method, Params, ExpiresIn):
        return f"https://storage.example/{Params['Key']}?ttl={ExpiresIn}&method={method}"

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key))


def test_local_storage_rejects_path_traversal(tmp_path):
    storage = LocalPrivateArtifactStorage(tmp_path / "exports")
    source = tmp_path / "source.zip"
    source.write_bytes(b"archive")
    with pytest.raises(ValueError, match="非法档案对象键"):
        storage.store_file(source, "../outside.zip")


def test_s3_storage_records_digest_verifies_and_presigns(monkeypatch, tmp_path):
    fake = FakeS3Client()
    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=lambda *_args, **_kwargs: fake))
    settings = get_settings()
    monkeypatch.setattr(settings, "artifact_storage_bucket", "private-archive")
    monkeypatch.setattr(settings, "artifact_storage_prefix", "production/exports")
    monkeypatch.setattr(settings, "artifact_storage_region", "cn-test-1")
    monkeypatch.setattr(settings, "artifact_storage_sse", "AES256")
    source = tmp_path / "part.zip"
    source.write_bytes(b"private archive")

    storage = S3PrivateArtifactStorage()
    stored = storage.store_file(source, "project/job/part-01.zip")
    assert stored.backend == "s3" and len(stored.sha256) == 64
    assert not source.exists()
    assert storage.verify(stored.object_key, stored.sha256, stored.size_bytes)
    assert "ttl=300" in storage.download_url(stored.object_key, "项目档案.zip")
    assert storage.delete(stored.object_key)


def test_legacy_single_artifact_job_keeps_download_contract():
    job = ProjectExportJob(
        id="legacy-job",
        project_id="project",
        requested_by_user_id="owner",
        status="succeeded",
        object_key="project/legacy.zip",
        file_size_bytes=128,
        artifact_sha256="a" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    payload = export_job_payload(job)
    assert payload["part_count"] == 1
    assert payload["artifacts"][0]["id"] == "legacy"
    assert payload["artifacts"][0]["download_path"].endswith("/download")
