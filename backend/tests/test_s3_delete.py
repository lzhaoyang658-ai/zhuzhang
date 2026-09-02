import pytest

from app.services.s3_delete import (delete_s3_keys_permanently,
                                    delete_s3_prefix,
                                    prune_s3_key_noncurrent_versions)


class VersionedFakeS3:
    def __init__(self, *, fail_key: str | None = None):
        self.fail_key = fail_key
        self.versions = [
            {"Key": "scope/project/file.csv", "VersionId": "v2"},
            {"Key": "scope/project/file.csv", "VersionId": "v1"},
        ]
        self.markers = [{"Key": "scope/project/old.pdf", "VersionId": "marker"}]
        self.current = [{"Key": "scope/project/current.pdf"}]

    def list_object_versions(self, **_request):
        return {"Versions": list(self.versions), "DeleteMarkers": list(self.markers), "IsTruncated": False}

    def list_objects_v2(self, **_request):
        return {"Contents": list(self.current), "IsTruncated": False}

    def delete_objects(self, Bucket, Delete):
        del Bucket
        keys = {item["Key"] for item in Delete["Objects"]}
        if self.fail_key and self.fail_key in keys:
            return {"Errors": [{"Key": self.fail_key, "Code": "AccessDenied"}]}
        version_pairs = {(item["Key"], item.get("VersionId")) for item in Delete["Objects"]}
        self.versions = [item for item in self.versions if (item["Key"], item["VersionId"]) not in version_pairs]
        self.markers = [item for item in self.markers if (item["Key"], item["VersionId"]) not in version_pairs]
        self.current = [item for item in self.current if item["Key"] not in keys]
        return {}


def test_delete_s3_prefix_removes_versions_markers_and_current_objects():
    client = VersionedFakeS3()

    assert delete_s3_prefix(client, "private", "scope/project/") == 4
    assert client.versions == []
    assert client.markers == []
    assert client.current == []


def test_delete_s3_prefix_fails_closed_on_partial_batch_errors():
    client = VersionedFakeS3(fail_key="scope/project/file.csv")

    with pytest.raises(RuntimeError, match="file.csv"):
        delete_s3_prefix(client, "private", "scope/project/")


def test_exact_key_cleanup_can_keep_only_the_latest_version():
    client = VersionedFakeS3()
    client.versions = [
        {"Key": "backups/latest.sqlite3", "VersionId": "v2", "IsLatest": True},
        {"Key": "backups/latest.sqlite3", "VersionId": "v1", "IsLatest": False},
    ]
    client.markers = []
    client.current = []

    assert prune_s3_key_noncurrent_versions(client, "private", "backups/latest.sqlite3") == 1
    assert [item["VersionId"] for item in client.versions] == ["v2"]
    assert delete_s3_keys_permanently(client, "private", ["backups/latest.sqlite3"]) == 1
    assert client.versions == []
