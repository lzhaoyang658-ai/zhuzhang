from __future__ import annotations

import os

from botocore.config import Config

from app.core.config import get_settings


settings = get_settings()


def create_private_s3_client():
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("S3 私有存储需要安装 boto3") from exc

    client_args: dict[str, object] = {
        "config": Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    }
    if settings.artifact_storage_region:
        client_args["region_name"] = settings.artifact_storage_region
    if settings.artifact_storage_endpoint_url:
        client_args["endpoint_url"] = settings.artifact_storage_endpoint_url
    if settings.artifact_storage_access_key_id:
        client_args["aws_access_key_id"] = settings.artifact_storage_access_key_id
    elif os.getenv("VOLCENGINE_ACCESS_KEY_ID"):
        client_args["aws_access_key_id"] = os.environ["VOLCENGINE_ACCESS_KEY_ID"]
    if settings.artifact_storage_secret_access_key:
        client_args["aws_secret_access_key"] = settings.artifact_storage_secret_access_key
    elif os.getenv("VOLCENGINE_ACCESS_KEY_SECRET"):
        client_args["aws_secret_access_key"] = os.environ["VOLCENGINE_ACCESS_KEY_SECRET"]
    session_token = os.getenv("VOLCENGINE_TOKEN")
    if session_token:
        client_args["aws_session_token"] = session_token
    return boto3.client("s3", **client_args)
