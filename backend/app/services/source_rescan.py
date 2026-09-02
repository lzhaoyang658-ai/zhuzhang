from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import Evidence, Project, Quote
from app.services.source_storage import get_source_storage
from app.services.upload_security import (
    MalwareScanner,
    UploadSecurityError,
    UploadSecurityLimits,
    create_malware_scanner,
    inspect_upload_bytes,
)


RecordKind = Literal["quote", "evidence"]
BASE_RESCAN_STATUSES = frozenset({"legacy_unscanned", "error"})


class SourceRescanError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RescanFailure:
    kind: RecordKind
    record_id: str
    project_id: str
    code: str

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "record_id": self.record_id,
            "project_id": self.project_id,
            "code": self.code,
        }


def _empty_kind_counts() -> dict[str, int]:
    return {"selected": 0, "clean": 0, "skipped": 0, "error": 0}


@dataclass
class SourceRescanSummary:
    project_id: str | None
    include_skipped: bool
    by_kind: dict[str, dict[str, int]] = field(default_factory=lambda: {
        "quote": _empty_kind_counts(),
        "evidence": _empty_kind_counts(),
    })
    failures: list[RescanFailure] = field(default_factory=list)
    projects_recalculated: int = 0
    source_file_count: int = 0
    source_bytes: int = 0

    def record_selected(self, kind: RecordKind) -> None:
        self.by_kind[kind]["selected"] += 1

    def record_success(self, kind: RecordKind, status: Literal["clean", "skipped"]) -> None:
        self.by_kind[kind][status] += 1

    def record_failure(self, kind: RecordKind, record_id: str, project_id: str, code: str) -> None:
        self.by_kind[kind]["error"] += 1
        self.failures.append(RescanFailure(kind, record_id, project_id, code))

    def as_dict(self) -> dict[str, object]:
        selected = sum(item["selected"] for item in self.by_kind.values())
        clean = sum(item["clean"] for item in self.by_kind.values())
        skipped = sum(item["skipped"] for item in self.by_kind.values())
        errors = sum(item["error"] for item in self.by_kind.values())
        return {
            "ok": errors == 0,
            "scope": {
                "project_id": self.project_id,
                "include_skipped": self.include_skipped,
            },
            "records": {
                "selected": selected,
                "processed": clean + skipped + errors,
                "clean": clean,
                "skipped": skipped,
                "error": errors,
                "by_kind": self.by_kind,
            },
            "usage": {
                "projects_recalculated": self.projects_recalculated,
                "source_file_count": self.source_file_count,
                "source_bytes": self.source_bytes,
            },
            "failures": [item.as_dict() for item in self.failures],
        }


def upload_security_limits_from_settings(settings: Settings) -> UploadSecurityLimits:
    return UploadSecurityLimits(
        max_file_bytes=settings.upload_max_file_bytes,
        zip_max_entries=settings.upload_zip_max_entries,
        zip_max_entry_bytes=settings.upload_zip_max_entry_bytes,
        zip_max_total_uncompressed_bytes=settings.upload_zip_max_total_bytes,
        image_max_pixels=settings.upload_image_max_pixels,
    )


def malware_scanner_from_settings(settings: Settings) -> MalwareScanner:
    return create_malware_scanner(
        settings.upload_malware_scan_mode,
        clamav_host=settings.clamav_host,
        clamav_port=settings.clamav_port,
        clamav_timeout_seconds=settings.clamav_timeout_seconds,
    )


def rescan_source_files(
    db: Session,
    *,
    project_id: str | None = None,
    include_skipped: bool = False,
    storage=None,
    scanner: MalwareScanner | None = None,
    limits: UploadSecurityLimits | None = None,
    scanned_at: datetime | None = None,
) -> dict[str, object]:
    settings = get_settings()
    storage = storage or get_source_storage()
    scanner = scanner or malware_scanner_from_settings(settings)
    limits = limits or upload_security_limits_from_settings(settings)
    scan_time = scanned_at or datetime.now(timezone.utc)
    statuses = set(BASE_RESCAN_STATUSES)
    if include_skipped:
        statuses.add("skipped")

    project_statement = select(Project).order_by(Project.id)
    if project_id:
        project_statement = project_statement.where(Project.id == project_id)
    projects = list(db.scalars(project_statement).all())
    if project_id and not projects:
        raise SourceRescanError("PROJECT_NOT_FOUND", "指定项目不存在")

    summary = SourceRescanSummary(project_id=project_id, include_skipped=include_skipped)
    for project in projects:
        quote_rows = db.scalars(
            select(Quote)
            .where(Quote.project_id == project.id, Quote.scan_status.in_(statuses))
            .order_by(Quote.id)
        ).all()
        evidence_rows = db.scalars(
            select(Evidence)
            .where(Evidence.project_id == project.id, Evidence.scan_status.in_(statuses))
            .order_by(Evidence.id)
        ).all()
        for item in quote_rows:
            _rescan_record(item, "quote", storage, scanner, limits, scan_time, summary)
        for item in evidence_rows:
            _rescan_record(item, "evidence", storage, scanner, limits, scan_time, summary)

    # Production sessions disable autoflush; persist inspected sizes before the
    # aggregate queries so project quota usage is calculated from fresh data.
    db.flush()
    _recalculate_project_usage(db, projects, summary)
    db.commit()
    return summary.as_dict()


def _rescan_record(
    item: Quote | Evidence,
    kind: RecordKind,
    storage,
    scanner: MalwareScanner,
    limits: UploadSecurityLimits,
    scan_time: datetime,
    summary: SourceRescanSummary,
) -> None:
    summary.record_selected(kind)
    object_key = _source_object_key(item, kind)
    try:
        content = storage.read_bytes(object_key)
    except FileNotFoundError:
        _mark_error(item, kind, scan_time, "SOURCE_FILE_MISSING", summary)
        return
    except Exception as exc:
        code = "SOURCE_FILE_MISSING" if _looks_like_missing_object(exc) else "SOURCE_FILE_READ_FAILED"
        _mark_error(item, kind, scan_time, code, summary)
        return

    suffix = Path(item.original_name).suffix.lower()
    try:
        inspection = inspect_upload_bytes(content, suffix, scanner=scanner, limits=limits)
    except UploadSecurityError as exc:
        _mark_error(item, kind, scan_time, exc.code, summary)
        return
    except Exception:
        _mark_error(item, kind, scan_time, "SOURCE_FILE_VALIDATION_FAILED", summary)
        return

    status: Literal["clean", "skipped"] = "clean" if inspection.scan_status == "clean" else "skipped"
    if kind == "quote":
        assert isinstance(item, Quote)
        item.source_sha256 = inspection.sha256
        item.source_size_bytes = inspection.size_bytes
        item.source_mime_type = inspection.detected_mime_type
    else:
        assert isinstance(item, Evidence)
        item.sha256 = inspection.sha256
        item.size_bytes = inspection.size_bytes
        item.mime_type = inspection.detected_mime_type
    item.scan_status = status
    item.scanned_at = scan_time
    summary.record_success(kind, status)


def _source_object_key(item: Quote | Evidence, kind: RecordKind) -> str:
    if kind == "quote":
        return f"{item.project_id}/quotes/{item.object_key}"
    return f"{item.project_id}/{item.object_key}"


def _mark_error(
    item: Quote | Evidence,
    kind: RecordKind,
    scan_time: datetime,
    code: str,
    summary: SourceRescanSummary,
) -> None:
    item.scan_status = "error"
    item.scanned_at = scan_time
    summary.record_failure(kind, item.id, item.project_id, code)


def _looks_like_missing_object(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error = response.get("Error")
    if not isinstance(error, dict):
        return False
    return str(error.get("Code", "")).casefold() in {"404", "nosuchkey", "notfound", "no_such_key"}


def _recalculate_project_usage(
    db: Session,
    projects: list[Project],
    summary: SourceRescanSummary,
) -> None:
    for project in projects:
        evidence_count, evidence_bytes = db.execute(
            select(func.count(Evidence.id), func.coalesce(func.sum(Evidence.size_bytes), 0)).where(
                Evidence.project_id == project.id
            )
        ).one()
        quote_count, quote_bytes = db.execute(
            select(func.count(Quote.id), func.coalesce(func.sum(Quote.source_size_bytes), 0)).where(
                Quote.project_id == project.id
            )
        ).one()
        project.source_file_count = int(evidence_count) + int(quote_count)
        project.source_bytes = int(evidence_bytes) + int(quote_bytes)
        summary.projects_recalculated += 1
        summary.source_file_count += project.source_file_count
        summary.source_bytes += project.source_bytes
