from __future__ import annotations


def _delete_batch(client, bucket: str, objects: list[dict[str, str]]) -> int:
    if not objects:
        return 0
    response = client.delete_objects(
        Bucket=bucket,
        Delete={"Objects": objects, "Quiet": True},
    ) or {}
    errors = response.get("Errors") or []
    if errors:
        details = ", ".join(str(item.get("Key", "unknown")) for item in errors[:5])
        raise RuntimeError(f"对象存储批量删除未完全成功：{details}")
    return len(objects)


def delete_s3_prefix(client, bucket: str, prefix: str) -> int:
    """Delete current objects plus all versions/delete markers under a prefix."""
    removed = 0
    list_versions = getattr(client, "list_object_versions", None)
    if callable(list_versions):
        key_marker: str | None = None
        version_marker: str | None = None
        while True:
            request: dict[str, object] = {"Bucket": bucket, "Prefix": prefix}
            if key_marker:
                request["KeyMarker"] = key_marker
            if version_marker:
                request["VersionIdMarker"] = version_marker
            page = list_versions(**request)
            versioned = [
                {"Key": item["Key"], "VersionId": item["VersionId"]}
                for item in [*(page.get("Versions") or []), *(page.get("DeleteMarkers") or [])]
            ]
            removed += _delete_batch(client, bucket, versioned)
            if not page.get("IsTruncated"):
                break
            key_marker = page.get("NextKeyMarker")
            version_marker = page.get("NextVersionIdMarker")
            if not key_marker:
                raise RuntimeError("对象存储版本列表分页缺少 NextKeyMarker")

    continuation: str | None = None
    while True:
        request = {"Bucket": bucket, "Prefix": prefix}
        if continuation:
            request["ContinuationToken"] = continuation
        page = client.list_objects_v2(**request)
        objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
        removed += _delete_batch(client, bucket, objects)
        if not page.get("IsTruncated"):
            break
        continuation = page.get("NextContinuationToken")
        if not continuation:
            raise RuntimeError("对象存储列表分页缺少 NextContinuationToken")
    return removed


def delete_s3_keys_permanently(client, bucket: str, keys: list[str]) -> int:
    removed = 0
    list_versions = getattr(client, "list_object_versions", None)
    for key in keys:
        if not callable(list_versions):
            removed += _delete_batch(client, bucket, [{"Key": key}])
            continue
        versions: list[dict[str, str]] = []
        key_marker: str | None = None
        version_marker: str | None = None
        while True:
            request: dict[str, object] = {"Bucket": bucket, "Prefix": key}
            if key_marker:
                request["KeyMarker"] = key_marker
            if version_marker:
                request["VersionIdMarker"] = version_marker
            page = list_versions(**request)
            for item in [*(page.get("Versions") or []), *(page.get("DeleteMarkers") or [])]:
                if item.get("Key") == key:
                    versions.append({"Key": key, "VersionId": item["VersionId"]})
            if not page.get("IsTruncated"):
                break
            key_marker = page.get("NextKeyMarker")
            version_marker = page.get("NextVersionIdMarker")
            if not key_marker:
                raise RuntimeError("对象存储版本列表分页缺少 NextKeyMarker")
        if versions:
            for offset in range(0, len(versions), 1000):
                removed += _delete_batch(client, bucket, versions[offset:offset + 1000])
        else:
            removed += _delete_batch(client, bucket, [{"Key": key}])
    return removed


def prune_s3_key_noncurrent_versions(client, bucket: str, key: str) -> int:
    list_versions = getattr(client, "list_object_versions", None)
    if not callable(list_versions):
        return 0
    stale: list[dict[str, str]] = []
    key_marker: str | None = None
    version_marker: str | None = None
    while True:
        request: dict[str, object] = {"Bucket": bucket, "Prefix": key}
        if key_marker:
            request["KeyMarker"] = key_marker
        if version_marker:
            request["VersionIdMarker"] = version_marker
        page = list_versions(**request)
        for item in [*(page.get("Versions") or []), *(page.get("DeleteMarkers") or [])]:
            if item.get("Key") == key and not item.get("IsLatest", False):
                stale.append({"Key": key, "VersionId": item["VersionId"]})
        if not page.get("IsTruncated"):
            break
        key_marker = page.get("NextKeyMarker")
        version_marker = page.get("NextVersionIdMarker")
        if not key_marker:
            raise RuntimeError("对象存储版本列表分页缺少 NextKeyMarker")
    removed = 0
    for offset in range(0, len(stale), 1000):
        removed += _delete_batch(client, bucket, stale[offset:offset + 1000])
    return removed
