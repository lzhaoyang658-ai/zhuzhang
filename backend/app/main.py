from __future__ import annotations

import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database import engine, get_db
from app.models import (AcceptanceRecord, AuditEvent, BaselineVersion, ChangeOrder,
                        Evidence, PaymentMilestone, PaymentRecord, Project, Quote,
                        QuoteCorrection, QuoteItem, QuoteMatchGroup, QuoteMatchMember,
                        QuoteParseJob, ProjectInvite, ProjectMembership, User, LoginSession,
                        ProjectBudgetCategory, ProjectFundLimitHistory, ProjectExportJob,
                        ProjectExportArtifact)
from app.models import Notification, NotificationPreference
from app.schemas import (AcceptanceCreate, BaselineCreate, ChangeAction, ChangeCreate,
                         MilestoneCreate, PaymentCreate, ProjectCreate, QuoteItemUpdate,
                         QuoteMatchGroupCreate, ProjectInviteAccept, ProjectInviteCreate,
                         ProjectMembershipUpdate, EmailCodeRequest, EmailCodeVerify,
                         EmailReauth)
from app.schemas.api import MAX_AMOUNT_CENTS
from app.schemas import ProjectUpdate, BudgetCategoryUpdate, ProjectDeletionRequest, ProjectExportCreate
from app.schemas import NotificationPreferenceUpdate
from app.services import (PENDING_STATUSES, calculate_budget, calculate_category_forecasts,
                          create_event_notifications, ensure_notification_preference,
                          evaluate_project_risks, notification_payload, record_event,
                          reconcile_project_notifications, signed_change)
from app.services.export_jobs import (delete_export_artifacts, export_artifact_download_url,
                                      export_artifact_path, export_job_payload,
                                      resume_incomplete_export_jobs,
                                      shutdown_export_jobs, submit_export_job,
                                      verify_export_artifact)
from app.services.quote_compare import build_quote_comparison
from app.services.quote_jobs import (quote_job_payload, resume_incomplete_jobs,
                                     shutdown_quote_jobs, submit_quote_job)
from app.services.seed import assert_production_demo_isolation, seed_demo
from app.services.auth import (aware, clear_auth_cookies, deliver_login_code,
                               issue_login_session, login_session_from_request,
                               request_ip, request_login_code, require_recent_login, utc_now,
                               validate_csrf, verify_login_code)
from app.services.maintenance import (run_maintenance_once,
                                      start_maintenance_scheduler,
                                      stop_maintenance_scheduler)
from app.services.worker_health import prune_stale_worker_heartbeats, queue_health_snapshot
from app.services.database_backup import database_backup_status, start_database_backup_scheduler, stop_database_backup_scheduler
from app.services.source_storage import get_source_storage
from app.services.schema_version import (assert_database_schema_current,
                                         database_schema_status,
                                         schema_mismatch_message)
from app.services.upload_security import (UploadSecurityError, UploadSecurityLimits,
                                           check_clamav_readiness,
                                           create_malware_scanner,
                                           inspect_upload_bytes)

settings = get_settings()

FILE_UPLOADS_DISABLED_DETAIL = {
    "code": "FILE_UPLOADS_DISABLED",
    "message": "当前部署未开启文件上传功能",
}


def ensure_file_uploads_enabled() -> None:
    if not settings.uploads_enabled:
        raise HTTPException(503, FILE_UPLOADS_DISABLED_DETAIL)


def is_file_upload_request(request: Request) -> bool:
    if request.method != "POST":
        return False
    parts = request.url.path.strip("/").split("/")
    return (
        len(parts) == 6
        and parts[:3] == ["api", "v1", "projects"]
        and parts[4:] == ["quotes", "import"]
    ) or (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "projects"]
        and parts[4] == "evidence"
    )


def upload_security_limits() -> UploadSecurityLimits:
    return UploadSecurityLimits(
        max_file_bytes=settings.upload_max_file_bytes,
        zip_max_entries=settings.upload_zip_max_entries,
        zip_max_entry_bytes=settings.upload_zip_max_entry_bytes,
        zip_max_total_uncompressed_bytes=settings.upload_zip_max_total_bytes,
        image_max_pixels=settings.upload_image_max_pixels,
    )


def inspect_api_upload(content: bytes, suffix: str, allowed_suffixes: set[str]):
    scanner = create_malware_scanner(
        settings.upload_malware_scan_mode,
        clamav_host=settings.clamav_host,
        clamav_port=settings.clamav_port,
        clamav_timeout_seconds=settings.clamav_timeout_seconds,
    )
    try:
        return inspect_upload_bytes(
            content,
            suffix,
            scanner=scanner,
            limits=upload_security_limits(),
            allowed_suffixes=allowed_suffixes,
        )
    except UploadSecurityError as exc:
        raise HTTPException(
            exc.status_code,
            {"code": exc.code, "message": exc.message},
        ) from exc


def reserve_source_quota(db: Session, project_id: str, size_bytes: int) -> Project:
    result = db.execute(
        update(Project)
        .where(
            Project.id == project_id,
            Project.source_file_count < settings.upload_project_max_files,
            Project.source_bytes <= settings.upload_project_max_bytes - size_bytes,
        )
        .values(
            source_file_count=Project.source_file_count + 1,
            source_bytes=Project.source_bytes + size_bytes,
        )
        .execution_options(synchronize_session=False)
    )
    project = db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .execution_options(populate_existing=True)
    )
    if not project:
        raise HTTPException(404, {"code": "PROJECT_NOT_FOUND", "message": "项目不存在"})
    if result.rowcount == 1:
        return project
    if project.source_file_count >= settings.upload_project_max_files:
        raise HTTPException(409, {
            "code": "PROJECT_FILE_QUOTA_EXCEEDED",
            "message": f"每个项目最多保存 {settings.upload_project_max_files} 个源文件",
        })
    if project.source_bytes + size_bytes > settings.upload_project_max_bytes:
        raise HTTPException(413, {
            "code": "PROJECT_STORAGE_QUOTA_EXCEEDED",
            "message": "项目源文件存储空间已达到上限",
        })
    raise HTTPException(409, {
        "code": "PROJECT_QUOTA_CONFLICT",
        "message": "项目文件配额已被其他请求更新，请重试",
    })


def source_scan_status(scan_status: str) -> str:
    return "clean" if scan_status == "clean" else "skipped"


def ensure_source_download_allowed(scan_status: str) -> None:
    blocked = scan_status in {"pending", "infected", "error"}
    if settings.app_env == "production" and scan_status != "clean":
        blocked = True
    if blocked:
        raise HTTPException(423, {
            "code": "SOURCE_FILE_NOT_CLEARED",
            "message": "文件尚未通过安全扫描，当前不可下载",
        })


def delete_source_best_effort(storage, object_key: str, version_id: str | None) -> None:
    try:
        storage.delete(object_key, version_id)
    except Exception:
        pass


EVIDENCE_RELATION_MODELS = {
    "milestone": PaymentMilestone,
    "payment": PaymentRecord,
    "acceptance": AcceptanceRecord,
    "change": ChangeOrder,
    "quote": Quote,
    "baseline": BaselineVersion,
}


def validate_evidence_relation(
    db: Session,
    project_id: str,
    related_type: str | None,
    related_id: str | None,
) -> tuple[str | None, str | None]:
    normalized_type = related_type.strip().lower() if related_type else None
    normalized_id = related_id.strip() if related_id else None
    if bool(normalized_type) != bool(normalized_id):
        raise HTTPException(422, {
            "code": "EVIDENCE_RELATION_INCOMPLETE",
            "message": "关联对象类型与 ID 必须同时提供",
        })
    if not normalized_type:
        return None, None
    model = EVIDENCE_RELATION_MODELS.get(normalized_type)
    if not model:
        raise HTTPException(422, {
            "code": "EVIDENCE_RELATION_TYPE_INVALID",
            "message": "不支持此关联对象类型",
        })
    related = db.get(model, normalized_id)
    if not related or related.project_id != project_id:
        raise HTTPException(404, {
            "code": "EVIDENCE_RELATION_NOT_FOUND",
            "message": "关联对象不存在或不属于当前项目",
        })
    return normalized_type, normalized_id


def normalize_upload_filename(raw_name: str | None, fallback: str) -> str:
    name = Path(raw_name or fallback).name.strip()
    if not name or name in {".", ".."} or len(name) > 240:
        raise HTTPException(422, {
            "code": "INVALID_FILE_NAME",
            "message": "文件名为空或过长",
        })
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise HTTPException(422, {
            "code": "INVALID_FILE_NAME",
            "message": "文件名包含非法控制字符",
        })
    return name


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate_runtime_safety()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    if settings.app_env != "test":
        assert_database_schema_current(engine)
    db = next(get_db())
    try:
        if settings.seed_demo_enabled:
            seed_demo(db)
        if settings.app_env == "production":
            assert_production_demo_isolation(db)
        prune_stale_worker_heartbeats(db)
    finally:
        db.close()
    resume_incomplete_jobs()
    run_maintenance_once()
    resume_incomplete_export_jobs()
    start_database_backup_scheduler()
    start_maintenance_scheduler()
    try:
        yield
    finally:
        stop_maintenance_scheduler()
        stop_database_backup_scheduler()
        shutdown_quote_jobs()
        shutdown_export_jobs()


def api_documentation_options() -> dict[str, str | None]:
    if settings.app_env == "production":
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {
        "docs_url": "/api/docs",
        "redoc_url": "/redoc",
        "openapi_url": "/openapi.json",
    }


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    lifespan=lifespan,
    **api_documentation_options(),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def maintenance_guard(request: Request, call_next):
    if settings.maintenance_mode and request.method in {"POST", "PATCH", "PUT", "DELETE"}:
        response = JSONResponse(
            status_code=503,
            headers={"Retry-After": "120"},
            content={"error": {"code": "MAINTENANCE_MODE", "message": "系统正在执行数据库切换，请稍后重试"}},
        )
    elif not settings.uploads_enabled and is_file_upload_request(request):
        # Block before FastAPI parses the multipart body, so disabled deployments
        # never read or spool untrusted file content.
        response = JSONResponse(
            status_code=503,
            content={"error": FILE_UPLOADS_DISABLED_DETAIL},
        )
    else:
        response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    if settings.app_env == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"error": {"code": "VALIDATION_ERROR", "message": "请检查填写内容", "details": jsonable_encoder(exc.errors())}})


@app.exception_handler(HTTPException)
async def http_error_handler(_: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "REQUEST_ERROR", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail})


def require_project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, {"code": "PROJECT_NOT_FOUND", "message": "项目不存在或无权访问"})
    return project


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    demo_user_id = request.headers.get("X-Demo-User-Id") if settings.demo_identity_enabled else None
    if demo_user_id:
        user = db.get(User, demo_user_id)
        if user and user.status == "active":
            request.state.demo_identity = True
            return user
    login_session = login_session_from_request(db, request)
    if login_session:
        user = login_session.user
        request.state.login_session = login_session
        if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            validate_csrf(request, login_session)
        if aware(login_session.last_seen_at) < utc_now() - timedelta(minutes=5):
            login_session.last_seen_at = utc_now()
            db.commit()
        if user.status == "active":
            return user
    raise HTTPException(401, {"code": "AUTH_REQUIRED", "message": "请先登录后继续"})


def require_access(db: Session, project_id: str, user: User, *, write: bool = False, owner: bool = False) -> tuple[Project, ProjectMembership]:
    project = require_project(db, project_id)
    membership = db.scalar(select(ProjectMembership).where(
        ProjectMembership.project_id == project_id,
        ProjectMembership.user_id == user.id,
        ProjectMembership.status == "active",
    ))
    if not membership:
        raise HTTPException(404, {"code": "PROJECT_NOT_FOUND", "message": "项目不存在或无权访问"})
    if owner and membership.role != "owner":
        raise HTTPException(403, {"code": "OWNER_REQUIRED", "message": "只有项目所有者可以执行此操作"})
    if write and membership.role == "viewer":
        raise HTTPException(403, {"code": "READ_ONLY_MEMBER", "message": "只读成员不能修改项目数据"})
    if write and project.status in {"已归档", "待删除", "删除中"}:
        raise HTTPException(409, {"code": "PROJECT_READ_ONLY", "message": "该项目当前为只读状态"})
    return project, membership


def require_milestone(db: Session, milestone_id: str) -> PaymentMilestone:
    item = db.get(PaymentMilestone, milestone_id)
    if not item:
        raise HTTPException(404, {"code": "MILESTONE_NOT_FOUND", "message": "付款节点不存在"})
    return item


def change_payload(item: ChangeOrder) -> dict:
    return {
        "id": item.id, "project_id": item.project_id, "change_type": item.change_type,
        "title": item.title, "reason": item.reason, "content": item.content,
        "amount_cents": item.amount_cents, "status": item.status, "version": item.version,
        "area": item.area, "proposer": item.proposer, "proposed_on": item.proposed_on,
        "category": item.category,
        "schedule_impact_days": item.schedule_impact_days, "confirmed_at": item.confirmed_at,
        "confirmation_name": item.confirmation_name, "updated_at": item.updated_at,
    }


def milestone_payload(db: Session, item: PaymentMilestone) -> dict:
    payment_rows = db.scalars(select(PaymentRecord).where(PaymentRecord.milestone_id == item.id)).all()
    paid = sum((-p.amount_cents if p.record_type == "reversal" else p.amount_cents) for p in payment_rows)
    latest = sorted(item.acceptances, key=lambda row: row.created_at, reverse=True)[0] if item.acceptances else None
    return {
        "id": item.id, "name": item.name, "planned_amount_cents": item.planned_amount_cents,
        "planned_date": item.planned_date, "condition": item.condition,
        "required_acceptance": item.required_acceptance, "paid_cents": paid,
        "acceptance": None if not latest else {"id": latest.id, "result": latest.result, "accepted_on": latest.accepted_on, "open_issues": latest.open_issues, "notes": latest.notes},
    }


def financial_request_fingerprint(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def payment_create_fingerprint(project_id: str, milestone_id: str, body: PaymentCreate) -> str:
    return financial_request_fingerprint({
        "action": "create_payment",
        "project_id": project_id,
        "milestone_id": milestone_id,
        "amount_cents": body.amount_cents,
        "paid_on": body.paid_on.isoformat(),
        "payee": body.payee,
        "method": body.method,
        "reference": body.reference,
        "override_reason": body.override_reason,
    })


def reverse_payment_fingerprint(original: PaymentRecord, reason: str) -> str:
    return financial_request_fingerprint({
        "action": "reverse_payment",
        "project_id": original.project_id,
        "payment_id": original.id,
        "reason": reason,
    })


def lock_financial_project(db: Session, project_id: str) -> Project:
    project = db.scalar(
        select(Project).where(Project.id == project_id).with_for_update()
    )
    if not project:
        raise HTTPException(404, {"code": "PROJECT_NOT_FOUND", "message": "项目不存在"})
    return project


def add_active_baseline(
    db: Session,
    *,
    project_id: str,
    amount_cents: int,
    reason: str,
    source_quote_id: str | None = None,
    confirmed_by: str = "系统任务",
) -> tuple[BaselineVersion, bool]:
    lock_financial_project(db, project_id)
    active_rows = db.scalars(
        select(BaselineVersion)
        .where(
            BaselineVersion.project_id == project_id,
            BaselineVersion.is_active.is_(True),
        )
        .order_by(BaselineVersion.version.desc())
        .with_for_update()
    ).all()
    current = active_rows[0] if active_rows else None
    if (
        source_quote_id is not None
        and current
        and current.source_quote_id == source_quote_id
        and current.amount_cents == amount_cents
    ):
        return current, False

    latest_version = db.scalar(
        select(func.max(BaselineVersion.version)).where(
            BaselineVersion.project_id == project_id
        )
    ) or 0
    for row in active_rows:
        row.is_active = False
    db.flush()

    baseline = BaselineVersion(
        project_id=project_id,
        version=latest_version + 1,
        amount_cents=amount_cents,
        reason=reason,
        source_quote_id=source_quote_id,
        is_active=True,
        confirmed_by=confirmed_by,
    )
    db.add(baseline)
    db.flush()
    return baseline, True


def payment_check(
    db: Session,
    milestone: PaymentMilestone,
    proposed_amount_cents: int = 0,
) -> dict:
    project = require_project(db, milestone.project_id)
    budget = calculate_budget(db, project)
    latest = db.scalar(select(AcceptanceRecord).where(AcceptanceRecord.milestone_id == milestone.id).order_by(AcceptanceRecord.created_at.desc()))
    pending = db.scalar(select(func.count(ChangeOrder.id)).where(ChangeOrder.project_id == project.id, ChangeOrder.status.in_(PENDING_STATUSES))) or 0
    payment_rows = db.scalars(select(PaymentRecord).where(PaymentRecord.milestone_id == milestone.id)).all()
    paid_for_node = sum((-p.amount_cents if p.record_type == "reversal" else p.amount_cents) for p in payment_rows)
    acceptance_requirement = (milestone.required_acceptance or "").strip()
    acceptance_required = acceptance_requirement not in {"", "无", "无需验收", "不需要", "不需要验收"}
    acceptance_ok = not acceptance_required or bool(latest and latest.result != "failed")
    issues_ok = not acceptance_required or bool(latest and latest.open_issues == 0)
    paid_for_node_after = paid_for_node + proposed_amount_cents
    paid_for_project_after = budget["paid_cents"] + proposed_amount_cents
    approved_budget_cents = budget["approved_budget_cents"]
    checks = [
        {"key": "acceptance", "label": "有效验收记录", "ok": acceptance_ok, "detail": "本节点无需验收" if not acceptance_required else ("已记录" if latest else "尚未记录验收")},
        {"key": "issues", "label": "未关闭问题", "ok": issues_ok, "detail": "本节点无需验收" if not acceptance_required else ("无未关闭问题" if latest and latest.open_issues == 0 else f"{latest.open_issues if latest else 0} 项待处理")},
        {"key": "changes", "label": "相关待确认增项", "ok": pending == 0, "detail": "无待确认增项" if pending == 0 else f"项目仍有 {pending} 项待确认"},
        {"key": "node_amount", "label": "付款后未超节点计划", "ok": paid_for_node_after <= milestone.planned_amount_cents, "detail": f"付款后累计 ¥{paid_for_node_after/100:,.2f}"},
        {"key": "budget", "label": "付款后累计未超批准预算", "ok": paid_for_project_after <= approved_budget_cents, "detail": f"付款后累计 ¥{paid_for_project_after/100:,.2f}，批准预算 ¥{approved_budget_cents/100:,.2f}"},
    ]
    high = any(not item["ok"] for item in checks[:2])
    return {
        "result": "high_risk" if high else ("warning" if any(not item["ok"] for item in checks) else "ready"),
        "checks": checks,
        "planned_remaining_cents": max(0, milestone.planned_amount_cents - paid_for_node),
        "current_paid_cents": budget["paid_cents"],
        "proposed_amount_cents": proposed_amount_cents,
        "paid_after_cents": paid_for_project_after,
        "approved_budget_cents": approved_budget_cents,
        "overrun_cents": max(0, paid_for_project_after - approved_budget_cents),
    }


@app.get("/health")
def health(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok", "service": settings.app_name}


@app.get("/health/ready")
def readiness(response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    if not settings.uploads_enabled or settings.upload_malware_scan_mode == "disabled":
        malware_scanner = {"mode": "disabled", "status": "not_applicable"}
    else:
        scan_status = check_clamav_readiness(
            host=settings.clamav_host,
            port=settings.clamav_port,
            timeout_seconds=settings.clamav_readiness_timeout_seconds,
        )
        malware_scanner = {"mode": "clamav", "status": scan_status}
    try:
        db.execute(select(1))
        sqlite_foreign_keys = not settings.is_sqlite or db.scalar(text("PRAGMA foreign_keys")) == 1
        schema = database_schema_status(
            db.connection(),
            allow_unmanaged_test_database=settings.app_env == "test",
        )
        queues = queue_health_snapshot(db)
    except Exception:
        response.status_code = 503
        payload = {
            "status": "unavailable",
            "database": "unavailable",
            "schema": None,
            "malware_scanner": malware_scanner,
            "queues": {},
        }
        return {"status": payload["status"]} if settings.app_env == "production" else payload
    required_queues = []
    if settings.quote_execution_mode == "worker":
        required_queues.append(queues["quote"])
    if settings.export_execution_mode == "worker":
        required_queues.append(queues["export"])
    backup = database_backup_status()
    backup_healthy = backup["status"] in {"disabled", "not_applicable", "healthy"}
    workers_ready = not settings.health_require_workers or all(item["status"] == "healthy" for item in required_queues)
    malware_scanner_ready = malware_scanner["status"] != "unavailable"
    ready = (
        schema.ready
        and sqlite_foreign_keys
        and workers_ready
        and malware_scanner_ready
        and (backup_healthy or not settings.database_backup_require_ready)
    )
    if not ready:
        response.status_code = 503
    degraded = (
        not schema.ready
        or not sqlite_foreign_keys
        or not workers_ready
        or not malware_scanner_ready
        or not backup_healthy
    )
    payload = {
        "status": "degraded" if degraded else "ready",
        "database": "available",
        "schema": schema.as_dict() | ({"error": schema_mismatch_message(schema)} if not schema.ready else {}),
        "sqlite_foreign_keys": sqlite_foreign_keys if settings.is_sqlite else None,
        "backup": backup,
        "malware_scanner": malware_scanner,
        "queues": queues,
    }
    return {"status": payload["status"]} if settings.app_env == "production" else payload


@app.get("/api/v1/task-health")
def task_health(user: User = Depends(current_user), db: Session = Depends(get_db)):
    project_ids = set(db.scalars(select(ProjectMembership.project_id).where(
        ProjectMembership.user_id == user.id,
        ProjectMembership.status == "active",
    )).all())
    return queue_health_snapshot(db, project_ids)


def user_payload(user: User) -> dict:
    return {"id": user.id, "name": user.name, "email": user.email}


@app.post("/api/v1/auth/email/request-code", status_code=202)
def request_email_code(body: EmailCodeRequest, request: Request, db: Session = Depends(get_db)):
    challenge, code = request_login_code(db, request, body.email)
    deliver_login_code(challenge.email, code)
    payload = {"challenge_id": challenge.id, "expires_in_seconds": settings.auth_code_minutes * 60, "delivery": settings.auth_delivery_mode}
    if settings.app_env != "production" and settings.auth_delivery_mode == "development":
        payload["development_code"] = code
    return payload


@app.get("/api/v1/auth/status")
def auth_status(request: Request, db: Session = Depends(get_db)):
    login_session = login_session_from_request(db, request)
    return {"authenticated": bool(login_session and login_session.user.status == "active")}


@app.post("/api/v1/auth/email/verify")
def verify_email_code(body: EmailCodeVerify, request: Request, response: Response, db: Session = Depends(get_db)):
    user = verify_login_code(db, body.email, body.code)
    now = utc_now()
    if not user:
        fallback_name = body.email.split("@", 1)[0][:40]
        user = User(name=(body.name or fallback_name).strip(), email=body.email.strip().lower(), email_verified_at=now)
        db.add(user)
        db.flush()
    else:
        user.email_verified_at = now
        if body.name and not user.name.strip():
            user.name = body.name.strip()
    login_session = issue_login_session(db, request, response, user)
    db.commit()
    return {"user": user_payload(user), "session": {"id": login_session.id, "expires_at": login_session.expires_at}}


@app.post("/api/v1/auth/email/reauth")
def reauthenticate_email(body: EmailReauth, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    login_session = getattr(request.state, "login_session", None)
    if not login_session:
        raise HTTPException(401, {
            "code": "REAUTH_SESSION_REQUIRED",
            "message": "请使用当前登录会话重新验证邮箱",
        })
    normalized_email = body.email.strip().lower()
    if normalized_email != user.email.strip().lower():
        raise HTTPException(403, {
            "code": "REAUTH_EMAIL_MISMATCH",
            "message": "只能验证当前账号绑定的邮箱",
        })
    verified_user = verify_login_code(db, normalized_email, body.code)
    if not verified_user or verified_user.id != user.id:
        db.rollback()
        raise HTTPException(403, {
            "code": "REAUTH_USER_MISMATCH",
            "message": "验证码与当前账号不匹配",
        })
    authenticated_at = utc_now()
    login_session.authenticated_at = authenticated_at
    db.commit()
    return {"authenticated_at": authenticated_at}


@app.post("/api/v1/auth/logout", status_code=204)
def logout(response: Response, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    login_session = getattr(request.state, "login_session", None)
    if login_session:
        login_session.revoked_at = utc_now()
        db.commit()
    clear_auth_cookies(response)


@app.get("/api/v1/auth/sessions")
def list_login_sessions(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    current = getattr(request.state, "login_session", None)
    rows = db.scalars(select(LoginSession).where(LoginSession.user_id == user.id, LoginSession.revoked_at.is_(None)).order_by(LoginSession.last_seen_at.desc())).all()
    now = utc_now()
    return [{
        "id": item.id,
        "current": bool(current and current.id == item.id),
        "device": item.user_agent,
        "last_seen_at": item.last_seen_at,
        "created_at": item.created_at,
        "expires_at": item.expires_at,
    } for item in rows if aware(item.expires_at) > now]


@app.delete("/api/v1/auth/sessions/{session_id}", status_code=204)
def revoke_login_session(session_id: str, response: Response, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.scalar(select(LoginSession).where(LoginSession.id == session_id, LoginSession.user_id == user.id, LoginSession.revoked_at.is_(None)))
    if not item:
        raise HTTPException(404, {"code": "SESSION_NOT_FOUND", "message": "登录设备不存在或已经退出"})
    current = getattr(request.state, "login_session", None)
    if not current or current.id != item.id:
        require_recent_login(request)
    item.revoked_at = utc_now()
    db.commit()
    if current and current.id == item.id:
        clear_auth_cookies(response)


@app.get("/api/v1/session")
def session(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    memberships = db.scalars(select(ProjectMembership).where(ProjectMembership.user_id == user.id, ProjectMembership.status == "active")).all()
    return {
        "user": user_payload(user),
        "memberships": [{"project_id": item.project_id, "role": item.role} for item in memberships],
        "mode": "demo_header" if getattr(request.state, "demo_identity", False) else "secure_session",
        "capabilities": {"uploads_enabled": settings.uploads_enabled},
    }


def reconcile_user_notifications(db: Session, user: User) -> None:
    projects = db.scalars(select(Project).join(ProjectMembership).where(
        ProjectMembership.user_id == user.id,
        ProjectMembership.status == "active",
    )).all()
    for project in projects:
        reconcile_project_notifications(db, project)
    db.commit()


@app.get("/api/v1/notifications")
def list_notifications(project_id: str | None = None, status: str = Query("all", pattern="^(all|active|resolved)$"), user: User = Depends(current_user), db: Session = Depends(get_db)):
    reconcile_user_notifications(db, user)
    query = select(Notification, Project.name).join(Project, Project.id == Notification.project_id).join(
        ProjectMembership,
        (ProjectMembership.project_id == Notification.project_id) & (ProjectMembership.user_id == user.id) & (ProjectMembership.status == "active"),
    ).where(Notification.user_id == user.id)
    if project_id:
        query = query.where(Notification.project_id == project_id)
    if status != "all":
        query = query.where(Notification.status == status)
    rows = db.execute(query.order_by(Notification.last_triggered_at.desc())).all()
    items = [notification_payload(item, project_name) for item, project_name in rows]
    items.sort(key=lambda item: ({"critical": 0, "warning": 1, "attention": 2, "info": 3}.get(item["level"], 9), item["read_at"] is not None))
    active = [item for item in items if item["status"] == "active"]
    return {
        "summary": {
            "active": len(active),
            "unread": sum(1 for item in active if item["read_at"] is None),
            "critical": sum(1 for item in active if item["level"] == "critical"),
            "resolved": sum(1 for item in items if item["status"] == "resolved"),
        },
        "items": items,
    }


@app.get("/api/v1/notifications/unread-count")
def notification_unread_count(user: User = Depends(current_user), db: Session = Depends(get_db)):
    reconcile_user_notifications(db, user)
    count = db.scalar(select(func.count(Notification.id)).where(
        Notification.user_id == user.id,
        Notification.status == "active",
        Notification.read_at.is_(None),
    )) or 0
    critical = db.scalar(select(func.count(Notification.id)).where(
        Notification.user_id == user.id,
        Notification.status == "active",
        Notification.level == "critical",
    )) or 0
    return {"unread": count, "critical": critical}


@app.post("/api/v1/notifications/{notification_id}/read")
def mark_notification_read(notification_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.scalar(select(Notification).where(Notification.id == notification_id, Notification.user_id == user.id))
    if not item:
        raise HTTPException(404, {"code": "NOTIFICATION_NOT_FOUND", "message": "通知不存在"})
    now = utc_now()
    item.read_at = now
    if item.kind == "event":
        item.status = "resolved"
        item.resolved_at = now
    db.commit()
    return {"id": item.id, "read_at": item.read_at}


@app.post("/api/v1/notifications/actions/read-all")
def mark_all_notifications_read(project_id: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = select(Notification).where(
        Notification.user_id == user.id,
        Notification.status == "active",
        Notification.read_at.is_(None),
    )
    if project_id:
        require_access(db, project_id, user)
        query = query.where(Notification.project_id == project_id)
    now = utc_now()
    rows = db.scalars(query).all()
    for item in rows:
        item.read_at = now
        if item.kind == "event":
            item.status = "resolved"
            item.resolved_at = now
    db.commit()
    return {"updated": len(rows)}


@app.get("/api/v1/notification-preferences")
def get_notification_preferences(user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.scalar(select(NotificationPreference).where(NotificationPreference.user_id == user.id))
    if not item:
        return {"email_enabled": False, "email_digest_frequency": "off", "last_digest_at": None}
    return {"email_enabled": item.email_enabled, "email_digest_frequency": item.email_digest_frequency, "last_digest_at": item.last_digest_at}


@app.patch("/api/v1/notification-preferences")
def update_notification_preferences(body: NotificationPreferenceUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = ensure_notification_preference(db, user.id)
    item.email_enabled = body.email_enabled
    item.email_digest_frequency = body.email_digest_frequency if body.email_enabled else "off"
    db.commit()
    return {"email_enabled": item.email_enabled, "email_digest_frequency": item.email_digest_frequency, "last_digest_at": item.last_digest_at}


@app.get("/api/v1/projects")
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Project, ProjectMembership).join(ProjectMembership).where(ProjectMembership.user_id == user.id, ProjectMembership.status == "active").order_by(Project.created_at.desc())).all()
    result = []
    for project, membership in rows:
        budget = calculate_budget(db, project)
        result.append({
            "id": project.id, "name": project.name, "city": project.city,
            "area_sqm": project.area_sqm, "area_basis": project.area_basis,
            "renovation_type": project.renovation_type, "status": project.status,
            "fund_limit_cents": project.fund_limit_cents,
            "predicted_settlement_cents": budget["predicted_settlement_cents"],
            "paid_cents": budget["paid_cents"], "role": membership.role,
            "planned_end": project.planned_end,
            "deletion_scheduled_for": project.deletion_scheduled_for,
            "created_at": project.created_at,
        })
    return result


@app.post("/api/v1/projects", status_code=201)
def create_project(body: ProjectCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = Project(**body.model_dump())
    db.add(project)
    db.flush()
    db.add(ProjectMembership(user_id=user.id, project_id=project.id, role="owner"))
    db.add(ProjectFundLimitHistory(project_id=project.id, previous_cents=None, new_cents=project.fund_limit_cents, reason="创建项目", changed_by_user_id=user.id, changed_by_name=user.name))
    category_names = ["拆除与新建", "水电", "泥瓦", "木作", "油漆", "门窗", "厨卫", "主材", "软装", "家具家电", "设计与管理", "其他"]
    db.add_all([ProjectBudgetCategory(project_id=project.id, name=name, sort_order=order) for order, name in enumerate(category_names, 1)])
    default_names = [("开工款", 20, 0), ("水电阶段款", 30, 25), ("泥木阶段款", 25, 50), ("竣工款", 20, 80), ("尾款", 5, 110)]
    for order, (name, percent, offset) in enumerate(default_names, 1):
        db.add(PaymentMilestone(project_id=project.id, name=name, planned_amount_cents=project.fund_limit_cents * percent // 100, planned_date=(project.planned_start or date.today()) + timedelta(days=offset), condition=f"{name.replace('款', '')}条件完成", sort_order=order))
    record_event(db, project_id=project.id, event_type="project_created", object_type="project", object_id=project.id, title="创建装修项目", detail=f"资金上限 ¥{project.fund_limit_cents / 100:,.0f}", actor=user.name)
    db.commit()
    return {"id": project.id}


def project_settings_payload(db: Session, project: Project, membership: ProjectMembership) -> dict:
    categories = db.scalars(select(ProjectBudgetCategory).where(ProjectBudgetCategory.project_id == project.id).order_by(ProjectBudgetCategory.sort_order)).all()
    category_forecasts = calculate_category_forecasts(db, project.id)
    history = db.scalars(select(ProjectFundLimitHistory).where(ProjectFundLimitHistory.project_id == project.id).order_by(ProjectFundLimitHistory.created_at.desc())).all()
    counts = {
        "quotes": db.scalar(select(func.count(Quote.id)).where(Quote.project_id == project.id)) or 0,
        "changes": db.scalar(select(func.count(ChangeOrder.id)).where(ChangeOrder.project_id == project.id)) or 0,
        "payments": db.scalar(select(func.count(PaymentRecord.id)).where(PaymentRecord.project_id == project.id)) or 0,
        "evidence": db.scalar(select(func.count(Evidence.id)).where(Evidence.project_id == project.id)) or 0,
    }
    return {
        "project": {
            "id": project.id, "name": project.name, "city": project.city,
            "area_sqm": project.area_sqm, "area_basis": project.area_basis,
            "renovation_type": project.renovation_type, "address": project.address,
            "notes": project.notes, "planned_start": project.planned_start,
            "planned_end": project.planned_end, "fund_limit_cents": project.fund_limit_cents,
            "reserve_cents": project.reserve_cents, "status": project.status,
            "archived_at": project.archived_at,
            "deletion_scheduled_for": project.deletion_scheduled_for,
            "source_file_count": project.source_file_count,
            "source_bytes": project.source_bytes,
            "source_file_limit": settings.upload_project_max_files,
            "source_bytes_limit": settings.upload_project_max_bytes,
        },
        "role": membership.role,
        "categories": [{"id": item.id, "name": item.name, "planned_limit_cents": item.planned_limit_cents, "forecast_cents": category_forecasts.get(item.name, 0), "sort_order": item.sort_order} for item in categories],
        "fund_limit_history": [{"id": item.id, "previous_cents": item.previous_cents, "new_cents": item.new_cents, "reason": item.reason, "changed_by_name": item.changed_by_name, "created_at": item.created_at} for item in history],
        "data_counts": counts,
    }


@app.get("/api/v1/projects/{project_id}/settings")
def get_project_settings(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project, membership = require_access(db, project_id, user)
    return project_settings_payload(db, project, membership)


@app.patch("/api/v1/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_recent_login(request)
    project, membership = require_access(db, project_id, user, owner=True)
    if project.status in {"已归档", "待删除"}:
        raise HTTPException(409, {"code": "PROJECT_READ_ONLY", "message": "请先恢复项目，再修改资料"})
    changes = body.model_dump(exclude_unset=True)
    reason = changes.pop("fund_limit_reason", None)
    next_start = changes.get("planned_start", project.planned_start)
    next_end = changes.get("planned_end", project.planned_end)
    if next_start and next_end and next_end < next_start:
        raise HTTPException(422, {"code": "INVALID_PROJECT_DATES", "message": "计划完工日期不能早于开工日期"})
    previous_limit = project.fund_limit_cents
    next_limit = changes.get("fund_limit_cents", previous_limit)
    next_reserve = changes.get("reserve_cents", project.reserve_cents)
    if next_reserve > next_limit:
        raise HTTPException(422, {"code": "RESERVE_EXCEEDS_FUND_LIMIT", "message": "风险预留金不能高于资金上限"})
    if next_limit != previous_limit and not reason:
        raise HTTPException(422, {"code": "FUND_LIMIT_REASON_REQUIRED", "message": "修改资金上限时需要填写原因"})
    for key, value in changes.items():
        if value is not None or key in {"address", "planned_start", "planned_end"}:
            setattr(project, key, value)
    if project.fund_limit_cents != previous_limit:
        db.add(ProjectFundLimitHistory(project_id=project.id, previous_cents=previous_limit, new_cents=project.fund_limit_cents, reason=reason, changed_by_user_id=user.id, changed_by_name=user.name, created_at=utc_now()))
        record_event(db, project_id=project.id, event_type="fund_limit_changed", object_type="project", object_id=project.id, title="装修资金上限已调整", detail=f"¥{previous_limit / 100:,.0f} → ¥{project.fund_limit_cents / 100:,.0f}；{reason}", amount_delta_cents=project.fund_limit_cents - previous_limit, actor=user.name)
    else:
        record_event(db, project_id=project.id, event_type="project_updated", object_type="project", object_id=project.id, title="项目资料已更新", actor=user.name)
    db.commit()
    return project_settings_payload(db, project, membership)


@app.patch("/api/v1/project-budget-categories/{category_id}")
def update_budget_category(category_id: str, body: BudgetCategoryUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    category = db.get(ProjectBudgetCategory, category_id)
    if not category:
        raise HTTPException(404, {"code": "CATEGORY_NOT_FOUND", "message": "预算类别不存在"})
    require_access(db, category.project_id, user, write=True)
    category.planned_limit_cents = body.planned_limit_cents
    record_event(db, project_id=category.project_id, event_type="category_limit_changed", object_type="budget_category", object_id=category.id, title=f"调整分类预算：{category.name}", detail="未设置" if body.planned_limit_cents is None else f"¥{body.planned_limit_cents / 100:,.0f}", actor=user.name)
    db.commit()
    return {"id": category.id, "name": category.name, "planned_limit_cents": category.planned_limit_cents}


@app.post("/api/v1/projects/{project_id}/archive")
def archive_project(project_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_recent_login(request)
    project, _ = require_access(db, project_id, user, owner=True)
    if project.status == "待删除":
        raise HTTPException(409, {"code": "PROJECT_PENDING_DELETION", "message": "请先撤销删除申请"})
    if project.status == "已归档":
        return {"id": project.id, "status": project.status}
    project.status_before_deletion = None
    project.status = "已归档"
    project.archived_at = utc_now()
    record_event(db, project_id=project.id, event_type="project_archived", object_type="project", object_id=project.id, title="项目已归档", detail="项目进入只读状态，仍可查看和导出", actor=user.name)
    db.commit()
    return {"id": project.id, "status": project.status, "archived_at": project.archived_at}


@app.post("/api/v1/projects/{project_id}/reopen")
def reopen_project(project_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_recent_login(request)
    project, _ = require_access(db, project_id, user, owner=True)
    if project.status != "已归档":
        raise HTTPException(409, {"code": "PROJECT_NOT_ARCHIVED", "message": "只有已归档项目可以重新开启"})
    project.status = "施工中"
    project.archived_at = None
    record_event(db, project_id=project.id, event_type="project_reopened", object_type="project", object_id=project.id, title="项目已重新开启", actor=user.name)
    db.commit()
    return {"id": project.id, "status": project.status}


@app.post("/api/v1/projects/{project_id}/deletion-request", status_code=202)
def request_project_deletion(project_id: str, body: ProjectDeletionRequest, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_recent_login(request)
    project, _ = require_access(db, project_id, user, owner=True)
    if body.project_name.strip() != project.name:
        raise HTTPException(422, {"code": "PROJECT_NAME_MISMATCH", "message": "项目名称不匹配，未提交删除申请"})
    if project.status == "待删除":
        return {"id": project.id, "status": project.status, "deletion_scheduled_for": project.deletion_scheduled_for}
    now = utc_now()
    project.status_before_deletion = project.status
    project.status = "待删除"
    project.deletion_requested_at = now
    project.deletion_scheduled_for = now + timedelta(days=7)
    record_event(db, project_id=project.id, event_type="project_deletion_requested", object_type="project", object_id=project.id, title="项目已进入删除撤销期", detail="7 天内可以撤销；到期后删除业务数据与原始附件", actor=user.name)
    db.commit()
    return {"id": project.id, "status": project.status, "deletion_scheduled_for": project.deletion_scheduled_for}


@app.post("/api/v1/projects/{project_id}/deletion-cancel")
def cancel_project_deletion(project_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_recent_login(request)
    project, _ = require_access(db, project_id, user, owner=True)
    if project.status != "待删除":
        raise HTTPException(409, {"code": "PROJECT_NOT_PENDING_DELETION", "message": "项目没有待撤销的删除申请"})
    project.status = project.status_before_deletion or "施工中"
    project.status_before_deletion = None
    project.deletion_requested_at = None
    project.deletion_scheduled_for = None
    record_event(db, project_id=project.id, event_type="project_deletion_cancelled", object_type="project", object_id=project.id, title="项目删除申请已撤销", actor=user.name)
    db.commit()
    return {"id": project.id, "status": project.status}


def membership_payload(item: ProjectMembership) -> dict:
    return {
        "id": item.id,
        "user": {"id": item.user.id, "name": item.user.name, "email": item.user.email},
        "role": item.role,
        "status": item.status,
        "created_at": item.created_at,
    }


@app.get("/api/v1/projects/{project_id}/members")
def list_project_members(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _, current_membership = require_access(db, project_id, user)
    members = db.scalars(select(ProjectMembership).where(ProjectMembership.project_id == project_id, ProjectMembership.status == "active").order_by(ProjectMembership.created_at)).all()
    invites = db.scalars(select(ProjectInvite).where(ProjectInvite.project_id == project_id, ProjectInvite.status == "pending").order_by(ProjectInvite.created_at.desc())).all()
    now = datetime.now(timezone.utc)
    invite_payload = []
    for invite in invites:
        expired = invite.expires_at.replace(tzinfo=timezone.utc) < now
        invite_payload.append({"id": invite.id, "email": invite.email, "role": invite.role, "status": "expired" if expired else invite.status, "expires_at": invite.expires_at, "created_at": invite.created_at})
    return {"current_role": current_membership.role, "limit": 5, "members": [membership_payload(item) for item in members], "invites": invite_payload}


@app.post("/api/v1/projects/{project_id}/invites", status_code=201)
def create_project_invite(project_id: str, body: ProjectInviteCreate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_recent_login(request)
    project, _ = require_access(db, project_id, user, owner=True)
    email = body.email.strip().lower()
    active_collaborators = db.scalar(select(func.count(ProjectMembership.id)).where(ProjectMembership.project_id == project_id, ProjectMembership.status == "active", ProjectMembership.role != "owner")) or 0
    now = datetime.now(timezone.utc)
    pending_invites = db.scalar(select(func.count(ProjectInvite.id)).where(ProjectInvite.project_id == project_id, ProjectInvite.status == "pending", ProjectInvite.expires_at > now)) or 0
    if active_collaborators + pending_invites >= 5:
        raise HTTPException(409, {"code": "COLLABORATOR_LIMIT_REACHED", "message": "每个项目最多邀请 5 位家庭协作者"})
    existing_user = db.scalar(select(User).where(func.lower(User.email) == email))
    if existing_user and db.scalar(select(ProjectMembership.id).where(ProjectMembership.project_id == project_id, ProjectMembership.user_id == existing_user.id, ProjectMembership.status == "active")):
        raise HTTPException(409, {"code": "MEMBER_ALREADY_EXISTS", "message": "该邮箱已经是项目成员"})
    if db.scalar(select(ProjectInvite.id).where(ProjectInvite.project_id == project_id, func.lower(ProjectInvite.email) == email, ProjectInvite.status == "pending", ProjectInvite.expires_at > now)):
        raise HTTPException(409, {"code": "INVITE_ALREADY_PENDING", "message": "该邮箱已有待接受邀请"})
    token = secrets.token_urlsafe(32)
    invite = ProjectInvite(
        project_id=project_id,
        email=email,
        role=body.role,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        invited_by_user_id=user.id,
        expires_at=now + timedelta(days=7),
    )
    db.add(invite)
    db.flush()
    record_event(db, project_id=project_id, event_type="member_invited", object_type="project_invite", object_id=invite.id, title=f"邀请家庭协作者：{email}", detail=f"角色 {body.role}，7 天内有效", actor=user.name)
    db.commit()
    return {"id": invite.id, "project_name": project.name, "email": email, "role": invite.role, "token": token, "accept_path": f"/join/{token}", "expires_at": invite.expires_at}


def invite_by_token(db: Session, token: str) -> ProjectInvite:
    invite = db.scalar(select(ProjectInvite).where(ProjectInvite.token_hash == hashlib.sha256(token.encode()).hexdigest()))
    if not invite:
        raise HTTPException(404, {"code": "INVITE_NOT_FOUND", "message": "邀请链接无效"})
    if invite.status != "pending":
        raise HTTPException(409, {"code": "INVITE_ALREADY_USED", "message": "该邀请已经处理"})
    if invite.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        invite.status = "expired"
        db.commit()
        raise HTTPException(410, {"code": "INVITE_EXPIRED", "message": "邀请链接已过期"})
    return invite


def masked_email(email: str) -> str:
    local, separator, domain = email.strip().partition("@")
    if not separator:
        return "***"
    if len(local) <= 1:
        masked_local = "*"
    elif len(local) == 2:
        masked_local = f"{local[0]}*"
    else:
        masked_local = f"{local[0]}***{local[-1]}"
    return f"{masked_local}@{domain}"


def public_project_label() -> str:
    return "装修项目"


@app.get("/api/v1/invites/{token}")
def get_project_invite(token: str, db: Session = Depends(get_db)):
    invite = invite_by_token(db, token)
    project = require_project(db, invite.project_id)
    return {"project_name": public_project_label(), "email": masked_email(invite.email), "role": invite.role, "expires_at": invite.expires_at}


@app.post("/api/v1/invites/{token}/accept")
def accept_project_invite(
    token: str,
    _body: ProjectInviteAccept,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if not getattr(request.state, "login_session", None):
        raise HTTPException(401, {
            "code": "AUTH_REQUIRED",
            "message": "请先使用受邀邮箱登录后接受邀请",
        })
    invite = invite_by_token(db, token)
    if user.email.strip().lower() != invite.email.strip().lower():
        raise HTTPException(403, {
            "code": "INVITE_EMAIL_MISMATCH",
            "message": "当前登录邮箱与受邀邮箱不一致，请切换账号",
        })

    lock_financial_project(db, invite.project_id)
    accepted_at = datetime.now(timezone.utc)
    consumed = db.execute(
        update(ProjectInvite)
        .where(
            ProjectInvite.id == invite.id,
            ProjectInvite.token_hash == hashlib.sha256(token.encode()).hexdigest(),
            ProjectInvite.status == "pending",
            ProjectInvite.expires_at >= accepted_at,
        )
        .values(status="accepted", accepted_at=accepted_at)
        .execution_options(synchronize_session=False)
    )
    if consumed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, {
            "code": "INVITE_ALREADY_USED",
            "message": "该邀请已经处理",
        })

    existing = db.scalar(select(ProjectMembership).where(ProjectMembership.project_id == invite.project_id, ProjectMembership.user_id == user.id))
    if existing:
        existing.role = invite.role
        existing.status = "active"
        membership = existing
    else:
        membership = ProjectMembership(user_id=user.id, project_id=invite.project_id, role=invite.role)
        db.add(membership)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, {
            "code": "INVITE_ACCEPT_CONFLICT",
            "message": "项目成员关系已发生变化，请刷新后重试",
        }) from exc
    record_event(db, project_id=invite.project_id, event_type="member_joined", object_type="project_membership", object_id=membership.id, title=f"家庭协作者已加入：{user.name}", detail=f"角色 {membership.role}", actor=user.name)
    project = require_project(db, invite.project_id)
    create_event_notifications(
        db, project, code="MEMBER_JOINED", level="info",
        title=f"家庭协作者已加入：{user.name}",
        message=f"当前角色：{membership.role}。项目成员现在可以共同查看进展。",
        object_type="project_membership", object_id=membership.id,
        action_path=f"/?project={invite.project_id}",
    )
    db.commit()
    return {"user": {"id": user.id, "name": user.name, "email": user.email}, "project_id": invite.project_id, "role": membership.role}


@app.patch("/api/v1/project-memberships/{membership_id}")
def update_project_membership(membership_id: str, body: ProjectMembershipUpdate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_recent_login(request)
    membership = db.get(ProjectMembership, membership_id)
    if not membership or membership.status != "active":
        raise HTTPException(404, {"code": "MEMBERSHIP_NOT_FOUND", "message": "项目成员不存在"})
    require_access(db, membership.project_id, user, owner=True)
    if membership.role == "owner":
        raise HTTPException(409, {"code": "OWNER_ROLE_IMMUTABLE", "message": "项目所有者角色不能修改"})
    previous = membership.role
    membership.role = body.role
    record_event(db, project_id=membership.project_id, event_type="member_role_changed", object_type="project_membership", object_id=membership.id, title=f"调整成员权限：{membership.user.name}", detail=f"{previous} → {body.role}", actor=user.name)
    project = require_project(db, membership.project_id)
    create_event_notifications(
        db, project, code="MEMBER_ROLE_CHANGED", level="attention",
        title=f"成员权限已调整：{membership.user.name}",
        message=f"权限由 {previous} 调整为 {body.role}。",
        object_type="project_membership", object_id=membership.id,
        action_path=f"/?project={membership.project_id}",
        event_state=f"{previous}-to-{body.role}",
    )
    db.commit()
    return membership_payload(membership)


@app.delete("/api/v1/project-memberships/{membership_id}", status_code=204)
def revoke_project_membership(membership_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_recent_login(request)
    membership = db.get(ProjectMembership, membership_id)
    if not membership or membership.status != "active":
        raise HTTPException(404, {"code": "MEMBERSHIP_NOT_FOUND", "message": "项目成员不存在"})
    require_access(db, membership.project_id, user, owner=True)
    if membership.role == "owner":
        raise HTTPException(409, {"code": "OWNER_CANNOT_BE_REMOVED", "message": "项目所有者不能被移除"})
    membership.status = "revoked"
    record_event(db, project_id=membership.project_id, event_type="member_removed", object_type="project_membership", object_id=membership.id, title=f"移除家庭协作者：{membership.user.name}", detail=f"原角色 {membership.role}", actor=user.name)
    project = require_project(db, membership.project_id)
    create_event_notifications(
        db, project, code="MEMBER_REMOVED", level="attention",
        title=f"家庭协作者已移除：{membership.user.name}",
        message=f"原角色：{membership.role}。该成员已不能继续访问项目。",
        object_type="project_membership", object_id=membership.id,
        action_path=f"/?project={membership.project_id}",
        event_state="revoked",
    )
    db.commit()


@app.post("/api/v1/project-invites/{invite_id}/revoke", status_code=204)
def revoke_project_invite(invite_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_recent_login(request)
    invite = db.get(ProjectInvite, invite_id)
    if not invite or invite.status != "pending":
        raise HTTPException(404, {"code": "INVITE_NOT_FOUND", "message": "待处理邀请不存在"})
    require_access(db, invite.project_id, user, owner=True)
    invite.status = "revoked"
    record_event(db, project_id=invite.project_id, event_type="member_invite_revoked", object_type="project_invite", object_id=invite.id, title=f"撤销协作者邀请：{invite.email}", actor=user.name)
    db.commit()


@app.get("/api/v1/projects/{project_id}/dashboard")
def dashboard(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project, _ = require_access(db, project_id, user)
    budget = calculate_budget(db, project)
    changes = db.scalars(select(ChangeOrder).where(ChangeOrder.project_id == project.id).order_by(ChangeOrder.updated_at.desc())).all()
    milestones = db.scalars(select(PaymentMilestone).where(PaymentMilestone.project_id == project.id).order_by(PaymentMilestone.planned_date)).all()
    next_item = next((m for m in milestones if m.planned_date >= date.today()), milestones[-1] if milestones else None)
    events = db.scalars(select(AuditEvent).where(AuditEvent.project_id == project.id).order_by(AuditEvent.created_at.desc()).limit(6)).all()
    risk_alerts = evaluate_project_risks(db, project)
    reconcile_project_notifications(db, project)
    db.commit()
    return {
        "project": {"id": project.id, "name": project.name, "city": project.city, "area_sqm": project.area_sqm, "area_basis": project.area_basis, "renovation_type": project.renovation_type, "status": project.status, "planned_end": project.planned_end},
        "budget": budget,
        "alerts": [{"code": item["code"], "level": item["level"], "title": item["title"], "action": item["message"], "action_path": item["action_path"]} for item in risk_alerts],
        "changes": [change_payload(item) for item in changes[:5]],
        "next_milestone": milestone_payload(db, next_item) if next_item else None,
        "timeline": [{"id": e.id, "event_type": e.event_type, "title": e.title, "detail": e.detail, "actor": e.actor, "amount_delta_cents": e.amount_delta_cents, "created_at": e.created_at} for e in events],
    }


@app.post("/api/v1/projects/{project_id}/baseline", status_code=201)
def create_baseline(project_id: str, body: BaselineCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_access(db, project_id, user, write=True)
    try:
        item, _ = add_active_baseline(
            db,
            project_id=project_id,
            amount_cents=body.amount_cents,
            reason=body.reason,
            confirmed_by=user.name,
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, {
            "code": "BASELINE_CONFLICT",
            "message": "合同基线已被其他请求更新，请刷新后重试",
        }) from exc
    record_event(db, project_id=project_id, event_type="baseline_activated", object_type="baseline", object_id=item.id, title=f"合同基线 V{item.version} 已生效", detail=body.reason, amount_delta_cents=body.amount_cents, actor=user.name)
    db.commit()
    return {"id": item.id, "version": item.version}


@app.get("/api/v1/projects/{project_id}/quotes")
def list_quotes(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_access(db, project_id, user)
    quotes = db.scalars(select(Quote).where(Quote.project_id == project_id).order_by(Quote.created_at.desc())).all()
    payload = []
    for q in quotes:
        latest_job = max(q.parse_jobs, key=lambda job: job.created_at, default=None)
        payload.append({
            "id": q.id,
            "name": q.name,
            "original_name": q.original_name,
            "status": q.status,
            "total_cents": q.total_cents,
            "source_total_cents": q.source_total_cents,
            "difference_cents": q.total_cents - q.source_total_cents if q.source_total_cents is not None else None,
            "item_count": len(q.items),
            "low_confidence_count": sum(1 for row in q.items if row.confidence < 75),
            "input_type": q.input_type,
            "parse_method": q.parse_method,
            "page_count": q.page_count,
            "warnings": q.warnings,
            "error_message": q.error_message,
            "source_size_bytes": q.source_size_bytes,
            "source_sha256": q.source_sha256,
            "source_mime_type": q.source_mime_type,
            "scan_status": q.scan_status,
            "created_at": q.created_at,
            "parse_job": quote_job_payload(latest_job),
        })
    return payload


@app.get("/api/v1/projects/{project_id}/quotes/compare")
def compare_quotes(project_id: str, quote_ids: list[str] = Query(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_access(db, project_id, user)
    if len(set(quote_ids)) != len(quote_ids) or not 2 <= len(quote_ids) <= 3:
        raise HTTPException(422, {"code": "INVALID_QUOTE_SELECTION", "message": "请选择 2～3 份不同报价"})
    found = db.scalars(select(Quote).where(Quote.project_id == project_id, Quote.id.in_(quote_ids))).all()
    lookup = {quote.id: quote for quote in found}
    if any(quote_id not in lookup for quote_id in quote_ids):
        raise HTTPException(404, {"code": "QUOTE_NOT_FOUND", "message": "报价不存在或不属于当前项目"})
    manual_groups = db.scalars(select(QuoteMatchGroup).where(QuoteMatchGroup.project_id == project_id)).all()
    return build_quote_comparison([lookup[quote_id] for quote_id in quote_ids], manual_groups)


QUOTE_SUFFIXES = {".csv", ".xlsx", ".pdf", ".jpg", ".jpeg", ".png", ".heic"}


@app.post("/api/v1/projects/{project_id}/quotes/import", status_code=202)
async def import_quote(project_id: str, file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    ensure_file_uploads_enabled()
    require_access(db, project_id, user, write=True)
    original_name = normalize_upload_filename(file.filename, "候选报价")
    suffix = Path(original_name).suffix.lower()
    if suffix not in QUOTE_SUFFIXES:
        raise HTTPException(415, {"code": "QUOTE_TYPE_NOT_ALLOWED", "message": "支持 XLSX、CSV、PDF、JPG、JPEG、PNG 和 HEIC"})
    content = await file.read(settings.upload_max_file_bytes + 1)
    inspection = inspect_api_upload(content, suffix, QUOTE_SUFFIXES)
    project = db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if not project:
        raise HTTPException(404, {"code": "PROJECT_NOT_FOUND", "message": "项目不存在"})
    quote_count = db.scalar(select(func.count(Quote.id)).where(Quote.project_id == project_id, Quote.status != "parse_failed")) or 0
    if quote_count >= 3:
        raise HTTPException(409, {"code": "QUOTE_LIMIT_REACHED", "message": "一个项目最多保留 3 份候选报价"})
    reserve_source_quota(db, project_id, inspection.size_bytes)
    object_key = f"{uuid4().hex}{suffix}"
    full_object_key = f"{project_id}/quotes/{object_key}"
    storage = get_source_storage()
    receipt = None
    try:
        receipt = storage.store_bytes(
            full_object_key,
            inspection.content,
            inspection.detected_mime_type,
            {
                "sha256": inspection.sha256,
                "scan-status": source_scan_status(inspection.scan_status),
                "detected-type": inspection.detected_type,
            },
        )
        if not storage.verify(full_object_key, inspection.size_bytes, inspection.sha256):
            raise RuntimeError("原始文件写入后完整性校验失败")
        item = Quote(
            project_id=project.id,
            name=Path(original_name).stem,
            original_name=original_name,
            object_key=object_key,
            source_size_bytes=inspection.size_bytes,
            source_sha256=inspection.sha256,
            source_mime_type=inspection.detected_mime_type,
            scan_status=source_scan_status(inspection.scan_status),
            scanned_at=utc_now(),
            status="queued",
            total_cents=0,
        )
        db.add(item)
        db.flush()
        job = QuoteParseJob(
            project_id=project.id,
            quote_id=item.id,
            status="queued",
            progress=5,
            stage="等待解析",
            max_attempts=settings.quote_job_max_attempts,
        )
        db.add(job)
        db.flush()
        record_event(db, project_id=project.id, event_type="quote_parse_queued", object_type="quote", object_id=item.id, title=f"候选报价已排队：{item.name}", detail=original_name, actor=user.name)
        db.commit()
    except Exception as exc:
        db.rollback()
        if receipt is not None:
            delete_source_best_effort(storage, full_object_key, receipt.version_id)
        raise HTTPException(503, {
            "code": "SOURCE_FILE_PERSIST_FAILED",
            "message": "文件保存失败，未产生导入记录，请稍后重试",
        }) from exc
    submit_quote_job(job.id)
    return {"id": item.id, "status": item.status, "job": quote_job_payload(job)}


@app.get("/api/v1/quote-jobs/{job_id}")
def get_quote_job(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = db.get(QuoteParseJob, job_id)
    if not job:
        raise HTTPException(404, {"code": "QUOTE_JOB_NOT_FOUND", "message": "解析任务不存在"})
    require_access(db, job.project_id, user)
    return quote_job_payload(job)


@app.post("/api/v1/quote-jobs/{job_id}/retry", status_code=202)
def retry_quote_job(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = db.get(QuoteParseJob, job_id)
    if not job:
        raise HTTPException(404, {"code": "QUOTE_JOB_NOT_FOUND", "message": "解析任务不存在"})
    require_access(db, job.project_id, user, write=True)
    if job.status not in {"failed", "dead_letter"}:
        raise HTTPException(409, {"code": "QUOTE_JOB_NOT_FAILED", "message": "只有失败任务可以重试"})
    quote = db.get(Quote, job.quote_id)
    job.status = "queued"
    job.progress = 5
    job.stage = "重新排队"
    job.error_message = None
    job.finished_at = None
    job.attempt = 0
    job.max_attempts = settings.quote_job_max_attempts
    job.lease_owner = None
    job.lease_expires_at = None
    job.next_attempt_at = None
    if quote:
        quote.status = "queued"
        quote.error_message = None
    db.commit()
    submit_quote_job(job.id)
    return quote_job_payload(job)


@app.post("/api/v1/projects/{project_id}/quote-match-groups", status_code=201)
def create_quote_match_group(project_id: str, body: QuoteMatchGroupCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_access(db, project_id, user, write=True)
    items = db.scalars(select(QuoteItem).where(QuoteItem.id.in_(body.item_ids))).all()
    if len(items) != len(body.item_ids) or any(item.project_id != project_id for item in items):
        raise HTTPException(404, {"code": "QUOTE_ITEM_NOT_FOUND", "message": "报价条目不存在或不属于当前项目"})
    if len({item.quote_id for item in items}) != len(items):
        raise HTTPException(422, {"code": "SAME_QUOTE_MATCH", "message": "同一报价中的条目不能互相匹配"})
    existing = db.scalars(select(QuoteMatchMember).where(QuoteMatchMember.quote_item_id.in_(body.item_ids))).all()
    if existing:
        raise HTTPException(409, {"code": "QUOTE_ITEM_ALREADY_MATCHED", "message": "至少一个条目已经人工匹配，请先解除原关系"})

    item_lookup = {item.id: item for item in items}
    ordered_items = [item_lookup[item_id] for item_id in body.item_ids]
    group = QuoteMatchGroup(
        project_id=project_id,
        canonical_name=body.canonical_name or ordered_items[0].standard_name,
        created_by=user.name,
    )
    db.add(group)
    db.flush()
    db.add_all([
        QuoteMatchMember(
            group_id=group.id,
            project_id=project_id,
            quote_id=item.quote_id,
            quote_item_id=item.id,
        )
        for item in ordered_items
    ])
    record_event(
        db,
        project_id=project_id,
        event_type="quote_match_confirmed",
        object_type="quote_match_group",
        object_id=group.id,
        title=f"确认报价匹配：{group.canonical_name}",
        detail=f"关联 {len(ordered_items)} 份报价中的条目",
        actor=user.name,
    )
    db.commit()
    return {"id": group.id, "canonical_name": group.canonical_name, "item_ids": body.item_ids}


@app.delete("/api/v1/quote-match-groups/{group_id}", status_code=204)
def delete_quote_match_group(group_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    group = db.get(QuoteMatchGroup, group_id)
    if not group:
        raise HTTPException(404, {"code": "QUOTE_MATCH_NOT_FOUND", "message": "人工匹配关系不存在"})
    require_access(db, group.project_id, user, write=True)
    project_id = group.project_id
    canonical_name = group.canonical_name
    db.delete(group)
    record_event(
        db,
        project_id=project_id,
        event_type="quote_match_removed",
        object_type="quote_match_group",
        object_id=group_id,
        title=f"解除报价匹配：{canonical_name}",
        actor=user.name,
    )
    db.commit()


@app.get("/api/v1/quotes/{quote_id}")
def quote_detail(quote_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.get(Quote, quote_id)
    if not item:
        raise HTTPException(404, {"code": "QUOTE_NOT_FOUND", "message": "报价不存在"})
    require_access(db, item.project_id, user)
    return {
        "id": item.id,
        "name": item.name,
        "original_name": item.original_name,
        "status": item.status,
        "total_cents": item.total_cents,
        "source_total_cents": item.source_total_cents,
        "difference_cents": item.total_cents - item.source_total_cents if item.source_total_cents is not None else None,
        "input_type": item.input_type,
        "parse_method": item.parse_method,
        "parser_version": item.parser_version,
        "page_count": item.page_count,
        "warnings": item.warnings,
        "source_size_bytes": item.source_size_bytes,
        "source_sha256": item.source_sha256,
        "source_mime_type": item.source_mime_type,
        "scan_status": item.scan_status,
        "correction_count": len(item.corrections),
        "items": [
            {
                "id": row.id,
                "original_name": row.original_name,
                "standard_name": row.standard_name,
                "area": row.area,
                "category": row.category,
                "quantity": row.quantity_text,
                "unit": row.unit,
                "unit_price_cents": row.unit_price_cents,
                "total_cents": row.total_cents,
                "material_info": row.material_info,
                "craft_notes": row.craft_notes,
                "source_location": row.source_location,
                "source_excerpt": row.source_excerpt,
                "confidence": row.confidence,
                "field_confidences": row.field_confidences,
            }
            for row in item.items
        ],
    }


@app.get("/api/v1/quotes/{quote_id}/source")
def quote_source(quote_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.get(Quote, quote_id)
    if not item:
        raise HTTPException(404, {"code": "QUOTE_NOT_FOUND", "message": "报价不存在"})
    require_access(db, item.project_id, user)
    ensure_source_download_allowed(item.scan_status)
    storage = get_source_storage()
    object_key = f"{item.project_id}/quotes/{item.object_key}"
    if storage.backend == "s3":
        url = storage.download_url(object_key, item.original_name, item.source_mime_type or "application/octet-stream")
        if not url:
            raise HTTPException(404, {"code": "QUOTE_SOURCE_NOT_FOUND", "message": "报价原文件不存在"})
        return RedirectResponse(url)
    path = storage.ensure_local(object_key)
    if path:
        return FileResponse(path, filename=item.original_name)
    raise HTTPException(404, {"code": "QUOTE_SOURCE_NOT_FOUND", "message": "报价原文件不存在"})


@app.patch("/api/v1/quote-items/{item_id}")
def update_quote_item(item_id: str, body: QuoteItemUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    initial = db.get(QuoteItem, item_id)
    if not initial:
        raise HTTPException(404, {"code": "QUOTE_ITEM_NOT_FOUND", "message": "报价条目不存在"})
    require_access(db, initial.project_id, user, write=True)
    quote = db.scalar(
        select(Quote).where(Quote.id == initial.quote_id).with_for_update()
    )
    if not quote or quote.status != "reviewing":
        raise HTTPException(409, {
            "code": "QUOTE_NOT_REVIEWING",
            "message": "只有等待人工校对的报价可以修改",
        })
    row = db.scalar(
        select(QuoteItem)
        .where(QuoteItem.id == item_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if not row:
        raise HTTPException(404, {"code": "QUOTE_ITEM_NOT_FOUND", "message": "报价条目不存在"})
    field_map = {
        "standard_name": "standard_name",
        "area": "area",
        "category": "category",
        "quantity": "quantity_text",
        "unit": "unit",
        "unit_price_cents": "unit_price_cents",
        "total_cents": "total_cents",
        "material_info": "material_info",
        "craft_notes": "craft_notes",
    }
    changed_fields = []
    for request_name, model_name in field_map.items():
        value = getattr(body, request_name)
        if value is None or value == getattr(row, model_name):
            continue
        previous = getattr(row, model_name)
        setattr(row, model_name, value)
        db.add(QuoteCorrection(project_id=row.project_id, quote_id=row.quote_id, quote_item_id=row.id, field_name=request_name, previous_value=None if previous is None else str(previous), corrected_value=str(value), actor=user.name))
        changed_fields.append(request_name)
    try:
        quantity = float(row.quantity_text) if row.quantity_text else None
    except ValueError:
        quantity = None
    if quantity is not None and row.unit_price_cents is not None:
        calculated = round(quantity * row.unit_price_cents)
        if row.total_cents != calculated:
            previous = row.total_cents
            row.total_cents = calculated
            db.add(QuoteCorrection(project_id=row.project_id, quote_id=row.quote_id, quote_item_id=row.id, field_name="total_cents", previous_value=str(previous), corrected_value=str(calculated), actor="系统重算"))
            changed_fields.append("total_cents")
    confidences = dict(row.field_confidences or {})
    for field_name in changed_fields:
        confidences[field_name] = 100
    row.field_confidences = confidences
    row.confidence = min((value for key, value in confidences.items() if key in {"standard_name", "quantity", "unit_price_cents", "total_cents"}), default=row.confidence)
    quote.total_cents = sum(item.total_cents for item in quote.items)
    record_event(db, project_id=row.project_id, event_type="quote_item_corrected", object_type="quote_item", object_id=row.id, title=f"校对报价条目：{row.standard_name}", detail="、".join(changed_fields) if changed_fields else "无字段变化", actor=user.name)
    db.commit()
    return {"id": row.id, "total_cents": row.total_cents, "quote_total_cents": quote.total_cents, "confidence": row.confidence, "changed_fields": changed_fields}


@app.post("/api/v1/quotes/{quote_id}/confirm")
def confirm_quote(quote_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    initial = db.get(Quote, quote_id)
    if not initial:
        raise HTTPException(404, {"code": "QUOTE_NOT_FOUND", "message": "报价不存在"})
    require_access(db, initial.project_id, user, write=True)
    item = db.scalar(select(Quote).where(Quote.id == quote_id).with_for_update())
    if not item:
        raise HTTPException(404, {"code": "QUOTE_NOT_FOUND", "message": "报价不存在"})
    if item.status == "confirmed":
        return {
            "id": item.id,
            "status": item.status,
            "total_cents": item.total_cents,
            "already_confirmed": True,
        }
    if item.status == "parse_failed":
        raise HTTPException(409, {"code": "QUOTE_PARSE_FAILED", "message": "解析失败的报价不能确认"})
    if item.status != "reviewing":
        raise HTTPException(409, {
            "code": "QUOTE_NOT_READY",
            "message": "报价尚未完成解析，不能确认",
        })
    latest_job = db.scalar(
        select(QuoteParseJob)
        .where(QuoteParseJob.quote_id == item.id)
        .order_by(QuoteParseJob.created_at.desc(), QuoteParseJob.id.desc())
    )
    if not latest_job or latest_job.status != "succeeded":
        raise HTTPException(409, {
            "code": "QUOTE_NOT_READY",
            "message": "报价解析任务尚未成功完成，不能确认",
        })
    quote_items = db.scalars(
        select(QuoteItem).where(QuoteItem.quote_id == item.id).with_for_update()
    ).all()
    if not quote_items:
        raise HTTPException(409, {
            "code": "QUOTE_EMPTY",
            "message": "报价没有可确认的条目",
        })
    incomplete = [row.id for row in quote_items if not row.standard_name or not row.quantity_text or not row.unit or row.total_cents is None]
    if incomplete:
        raise HTTPException(409, {"code": "QUOTE_REVIEW_INCOMPLETE", "message": f"还有 {len(incomplete)} 个条目缺少项目名称、数量、单位或金额", "item_ids": incomplete})
    total_cents = sum(row.total_cents for row in quote_items)
    if total_cents <= 0:
        raise HTTPException(409, {
            "code": "QUOTE_TOTAL_INVALID",
            "message": "报价合计必须大于 0",
        })
    item.total_cents = total_cents
    item.status = "confirmed"
    record_event(db, project_id=item.project_id, event_type="quote_review_completed", object_type="quote", object_id=item.id, title=f"报价已完成校对：{item.name}", detail=f"{len(quote_items)} 个条目，合计 ¥{item.total_cents/100:,.2f}", actor=user.name)
    db.commit()
    return {"id": item.id, "status": item.status, "total_cents": item.total_cents, "already_confirmed": False}


@app.post("/api/v1/quotes/{quote_id}/activate-baseline", status_code=201)
def activate_quote_baseline(quote_id: str, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    initial = db.get(Quote, quote_id)
    if not initial:
        raise HTTPException(404, {"code": "QUOTE_NOT_FOUND", "message": "报价不存在"})
    require_access(db, initial.project_id, user, write=True)
    lock_financial_project(db, initial.project_id)
    quote = db.scalar(select(Quote).where(Quote.id == quote_id).with_for_update())
    if not quote:
        raise HTTPException(404, {"code": "QUOTE_NOT_FOUND", "message": "报价不存在"})
    if quote.status != "confirmed":
        raise HTTPException(409, {"code": "QUOTE_NOT_CONFIRMED", "message": "未完成校对的报价不能设为合同基线"})
    latest_job = db.scalar(
        select(QuoteParseJob)
        .where(QuoteParseJob.quote_id == quote.id)
        .order_by(QuoteParseJob.created_at.desc(), QuoteParseJob.id.desc())
    )
    if not latest_job or latest_job.status != "succeeded":
        raise HTTPException(409, {
            "code": "QUOTE_NOT_READY",
            "message": "报价解析任务未成功完成，不能设为合同基线",
        })
    quote_items = db.scalars(
        select(QuoteItem).where(QuoteItem.quote_id == quote.id).with_for_update()
    ).all()
    if not quote_items:
        raise HTTPException(409, {"code": "QUOTE_EMPTY", "message": "空报价不能设为合同基线"})
    incomplete = [row.id for row in quote_items if not row.standard_name or not row.quantity_text or not row.unit or row.total_cents is None]
    if incomplete:
        raise HTTPException(409, {
            "code": "QUOTE_REVIEW_INCOMPLETE",
            "message": f"还有 {len(incomplete)} 个报价条目不完整",
            "item_ids": incomplete,
        })
    recalculated_total = sum(row.total_cents for row in quote_items)
    if recalculated_total <= 0:
        raise HTTPException(409, {"code": "QUOTE_TOTAL_INVALID", "message": "报价合计必须大于 0"})
    if recalculated_total != quote.total_cents:
        raise HTTPException(409, {
            "code": "QUOTE_TOTAL_STALE",
            "message": "报价条目合计已变化，请重新确认后再激活",
        })
    try:
        baseline, created = add_active_baseline(
            db,
            project_id=quote.project_id,
            amount_cents=recalculated_total,
            reason=f"由候选报价“{quote.name}”确认",
            source_quote_id=quote.id,
            confirmed_by=user.name,
        )
    except IntegrityError as exc:
        project_id = quote.project_id
        db.rollback()
        current = db.scalar(
            select(BaselineVersion).where(
                BaselineVersion.project_id == project_id,
                BaselineVersion.is_active.is_(True),
            )
        )
        if current and current.source_quote_id == quote_id and current.amount_cents == recalculated_total:
            response.status_code = 200
            return {
                "id": current.id,
                "version": current.version,
                "amount_cents": current.amount_cents,
                "already_active": True,
            }
        raise HTTPException(409, {
            "code": "BASELINE_CONFLICT",
            "message": "合同基线已被其他请求更新，请刷新后重试",
        }) from exc
    if not created:
        db.commit()
        response.status_code = 200
        return {
            "id": baseline.id,
            "version": baseline.version,
            "amount_cents": baseline.amount_cents,
            "already_active": True,
        }
    record_event(db, project_id=quote.project_id, event_type="baseline_activated", object_type="baseline", object_id=baseline.id, title=f"合同基线 V{baseline.version} 已生效", detail=baseline.reason, amount_delta_cents=baseline.amount_cents, actor=user.name)
    db.commit()
    return {"id": baseline.id, "version": baseline.version, "amount_cents": baseline.amount_cents, "already_active": False}


@app.get("/api/v1/projects/{project_id}/changes")
def list_changes(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_access(db, project_id, user)
    items = db.scalars(select(ChangeOrder).where(ChangeOrder.project_id == project_id).order_by(ChangeOrder.updated_at.desc())).all()
    return [change_payload(item) for item in items]


@app.post("/api/v1/projects/{project_id}/changes", status_code=201)
def create_change(project_id: str, body: ChangeCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_access(db, project_id, user, write=True)
    item = ChangeOrder(project_id=project_id, **body.model_dump())
    db.add(item)
    db.flush()
    record_event(db, project_id=project_id, event_type="change_created", object_type="change", object_id=item.id, title=f"创建增减项：{item.title}", detail="草稿", amount_delta_cents=signed_change(item), actor=user.name)
    db.commit()
    return change_payload(item)


TRANSITIONS = {
    "send": ({"draft", "revising"}, "pending_confirmation", "已发送确认"),
    "approve": ({"pending_confirmation"}, "approved", "增减项已批准"),
    "reject": ({"pending_confirmation"}, "rejected", "增减项已拒绝"),
    "request_revision": ({"pending_confirmation"}, "revising", "确认人要求补充"),
    "withdraw": ({"pending_confirmation", "revising"}, "withdrawn", "增减项已撤回"),
    "implement": ({"approved"}, "implemented", "增减项已实施"),
    "accept": ({"implemented"}, "accepted", "增减项已验收"),
    "settle": ({"accepted"}, "settled", "增减项已结算"),
}


@app.post("/api/v1/changes/{change_id}/actions/{action}")
def act_on_change(change_id: str, action: str, body: ChangeAction, user: User = Depends(current_user), db: Session = Depends(get_db)):
    initial = db.get(ChangeOrder, change_id)
    if not initial:
        raise HTTPException(404, {"code": "CHANGE_NOT_FOUND", "message": "增减项不存在"})
    require_access(db, initial.project_id, user, write=True)
    lock_financial_project(db, initial.project_id)
    item = db.scalar(
        select(ChangeOrder)
        .where(ChangeOrder.id == change_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if not item:
        raise HTTPException(404, {"code": "CHANGE_NOT_FOUND", "message": "增减项不存在"})
    rule = TRANSITIONS.get(action)
    if not rule or item.status not in rule[0]:
        raise HTTPException(409, {"code": "INVALID_TRANSITION", "message": f"当前状态不能执行“{action}”"})
    item.status = rule[1]
    item.updated_at = datetime.now(timezone.utc)
    record_event(db, project_id=item.project_id, event_type=f"change_{action}", object_type="change", object_id=item.id, title=f"{rule[2]}：{item.title}", detail=body.comment, amount_delta_cents=signed_change(item) if action in {"approve", "reject"} else 0, actor=user.name)
    db.commit()
    return change_payload(item)


@app.post("/api/v1/changes/{change_id}/share")
def share_change(change_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.get(ChangeOrder, change_id)
    if not item:
        raise HTTPException(404, {"code": "CHANGE_NOT_FOUND", "message": "增减项不存在"})
    require_access(db, item.project_id, user, write=True)
    if item.status in {"draft", "revising"}:
        item.status = "pending_confirmation"
    elif item.status != "pending_confirmation":
        raise HTTPException(409, {"code": "INVALID_STATUS", "message": "当前状态不能发送确认"})
    token = secrets.token_urlsafe(32)
    item.share_token_hash = hashlib.sha256(token.encode()).hexdigest()
    item.share_expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    item.updated_at = datetime.now(timezone.utc)
    record_event(db, project_id=item.project_id, event_type="change_confirmation_sent", object_type="change", object_id=item.id, title=f"已发送确认：{item.title}", detail="链接 7 天内有效", actor=user.name)
    db.commit()
    return {"token": token, "expires_at": item.share_expires_at}


class ExternalDecision(BaseModel):
    decision: str = Field(pattern="^(approve|reject|request_revision)$")
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=80)
    comment: str = Field(default="", max_length=1000)
    current_version_confirmed: bool


def change_by_token(db: Session, token: str) -> ChangeOrder:
    digest = hashlib.sha256(token.encode()).hexdigest()
    item = db.scalar(select(ChangeOrder).where(ChangeOrder.share_token_hash == digest))
    if not item:
        raise HTTPException(404, {"code": "LINK_NOT_FOUND", "message": "确认链接无效"})
    expires = item.share_expires_at
    if expires and expires.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(410, {"code": "LINK_EXPIRED", "message": "确认链接已过期"})
    return item


@app.get("/api/v1/external/changes/{token}")
def external_change(token: str, db: Session = Depends(get_db)):
    item = change_by_token(db, token)
    project = require_project(db, item.project_id)
    return {"project_name": public_project_label(), "change": change_payload(item), "read_only": item.status != "pending_confirmation", "notice": "普通网页确认不等同于认证电子签名"}


@app.post("/api/v1/external/changes/{token}/decision")
def external_decision(token: str, body: ExternalDecision, request: Request, db: Session = Depends(get_db)):
    initial = change_by_token(db, token)
    lock_financial_project(db, initial.project_id)
    if not body.current_version_confirmed:
        raise HTTPException(422, {"code": "VERSION_NOT_CONFIRMED", "message": "请确认当前版本"})
    statuses = {"approve": "approved", "reject": "rejected", "request_revision": "revising"}
    confirmed_at = datetime.now(timezone.utc)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    result = db.execute(
        update(ChangeOrder)
        .where(
            ChangeOrder.id == initial.id,
            ChangeOrder.share_token_hash == token_hash,
            ChangeOrder.status == "pending_confirmation",
            ChangeOrder.version == body.version,
            (ChangeOrder.share_expires_at.is_(None) | (ChangeOrder.share_expires_at >= confirmed_at)),
        )
        .values(
            status=statuses[body.decision],
            confirmation_name=body.name,
            confirmation_role=body.role,
            confirmation_comment=body.comment,
            confirmed_at=confirmed_at,
            updated_at=confirmed_at,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, {
            "code": "CHANGE_DECISION_CONFLICT",
            "message": "该版本已处理或已发生变化，请刷新后查看最新状态",
        })
    item = db.scalar(
        select(ChangeOrder)
        .where(ChangeOrder.id == initial.id)
        .execution_options(populate_existing=True)
    )
    if not item:
        db.rollback()
        raise HTTPException(409, {
            "code": "CHANGE_DECISION_CONFLICT",
            "message": "该版本已处理或已发生变化，请刷新后查看最新状态",
        })
    ip = request_ip(request)
    ip_digest = hashlib.sha256(ip.encode()).hexdigest()[:10]
    record_event(db, project_id=item.project_id, event_type=f"change_{body.decision}", object_type="change", object_id=item.id, title=f"外部确认：{item.title}", detail=f"{body.name}（{body.role}），IP 摘要 {ip_digest}；{body.comment}", amount_delta_cents=signed_change(item) if body.decision == "approve" else 0, actor="外部自报")
    db.commit()
    return {"status": item.status, "confirmed_at": item.confirmed_at}


@app.get("/api/v1/projects/{project_id}/milestones")
def list_milestones(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_access(db, project_id, user)
    items = db.scalars(select(PaymentMilestone).where(PaymentMilestone.project_id == project_id).order_by(PaymentMilestone.sort_order, PaymentMilestone.planned_date)).all()
    return [milestone_payload(db, item) for item in items]


@app.post("/api/v1/projects/{project_id}/milestones", status_code=201)
def create_milestone(project_id: str, body: MilestoneCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_access(db, project_id, user, write=True)
    item = PaymentMilestone(project_id=project_id, **body.model_dump())
    db.add(item)
    db.flush()
    record_event(db, project_id=project_id, event_type="milestone_created", object_type="milestone", object_id=item.id, title=f"新增付款节点：{item.name}", actor=user.name)
    db.commit()
    return milestone_payload(db, item)


@app.post("/api/v1/milestones/{milestone_id}/acceptances", status_code=201)
def create_acceptance(milestone_id: str, body: AcceptanceCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    milestone = require_milestone(db, milestone_id)
    require_access(db, milestone.project_id, user, write=True)
    item = AcceptanceRecord(project_id=milestone.project_id, milestone_id=milestone.id, recorded_by=user.name, **body.model_dump())
    db.add(item)
    db.flush()
    labels = {"passed": "通过", "passed_with_issues": "带问题通过", "failed": "不通过"}
    record_event(db, project_id=milestone.project_id, event_type="acceptance_recorded", object_type="acceptance", object_id=item.id, title=f"{milestone.name}验收：{labels[item.result]}", detail=item.notes, actor=user.name)
    db.commit()
    return {"id": item.id, "result": item.result}


@app.get("/api/v1/milestones/{milestone_id}/payment-check")
def get_payment_check(milestone_id: str, proposed_amount_cents: int = Query(default=0, ge=0, le=MAX_AMOUNT_CENTS), user: User = Depends(current_user), db: Session = Depends(get_db)):
    milestone = require_milestone(db, milestone_id)
    require_access(db, milestone.project_id, user)
    return payment_check(db, milestone, proposed_amount_cents)


@app.get("/api/v1/projects/{project_id}/payments")
def list_payments(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_access(db, project_id, user)
    items = db.scalars(
        select(PaymentRecord)
        .where(PaymentRecord.project_id == project_id)
        .order_by(PaymentRecord.paid_on.desc(), PaymentRecord.created_at.desc())
    ).all()
    milestone_names = {
        item.id: item.name
        for item in db.scalars(select(PaymentMilestone).where(PaymentMilestone.project_id == project_id)).all()
    }
    return [{
        "id": item.id,
        "milestone_id": item.milestone_id,
        "milestone_name": milestone_names.get(item.milestone_id, "未知节点"),
        "amount_cents": item.amount_cents,
        "paid_on": item.paid_on,
        "payee": item.payee,
        "method": item.method,
        "reference": item.reference,
        "record_type": item.record_type,
        "reversal_of_payment_id": item.reversal_of_payment_id,
        "controlled": item.controlled,
        "override_reason": item.override_reason,
        "created_at": item.created_at,
    } for item in items]


@app.post("/api/v1/milestones/{milestone_id}/payments", status_code=201)
def create_payment(milestone_id: str, body: PaymentCreate, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    milestone = require_milestone(db, milestone_id)
    require_access(db, milestone.project_id, user, write=True)
    lock_financial_project(db, milestone.project_id)
    milestone = db.scalar(
        select(PaymentMilestone).where(PaymentMilestone.id == milestone_id).with_for_update()
    )
    if not milestone:
        raise HTTPException(404, {"code": "MILESTONE_NOT_FOUND", "message": "付款节点不存在"})
    fingerprint = payment_create_fingerprint(milestone.project_id, milestone.id, body)
    existing = db.scalar(select(PaymentRecord).where(
        PaymentRecord.project_id == milestone.project_id,
        PaymentRecord.idempotency_key == body.idempotency_key,
    ))
    if existing:
        if existing.request_fingerprint == fingerprint and existing.record_type == "normal":
            response.status_code = 200
            return {
                "id": existing.id,
                "controlled": existing.controlled,
                "message": "该笔付款已记录",
                "replayed": True,
            }
        raise HTTPException(409, {
            "code": "IDEMPOTENCY_KEY_REUSED",
            "message": "同一请求标识不能用于不同的付款内容",
        })
    check = payment_check(db, milestone, body.amount_cents)
    remaining_cents = check["planned_remaining_cents"]
    if remaining_cents <= 0:
        raise HTTPException(409, {"code": "MILESTONE_ALREADY_PAID", "message": "该付款节点已经付清，如需调整请先冲正原记录"})
    if body.amount_cents > remaining_cents:
        raise HTTPException(409, {"code": "PAYMENT_EXCEEDS_REMAINING", "message": f"本次付款不能超过节点剩余金额 ¥{remaining_cents / 100:,.2f}"})
    budget_check = next(item for item in check["checks"] if item["key"] == "budget")
    if not budget_check["ok"] and not body.override_reason:
        raise HTTPException(409, {
            "code": "PAYMENT_EXCEEDS_APPROVED_BUDGET",
            "message": "本次付款会超过项目批准预算；如确需记录，请填写继续记录原因",
            "approved_budget_cents": check["approved_budget_cents"],
            "current_paid_cents": check["current_paid_cents"],
            "paid_after_cents": check["paid_after_cents"],
            "overrun_cents": check["overrun_cents"],
        })
    if check["result"] == "high_risk" and not body.override_reason:
        raise HTTPException(409, {"code": "OVERRIDE_REASON_REQUIRED", "message": "高风险付款需要填写继续记录原因"})
    controlled = check["result"] != "high_risk"
    item = PaymentRecord(project_id=milestone.project_id, milestone_id=milestone.id, amount_cents=body.amount_cents, paid_on=body.paid_on, payee=body.payee, method=body.method, reference=body.reference, controlled=controlled, override_reason=body.override_reason, idempotency_key=body.idempotency_key, request_fingerprint=fingerprint)
    project_id = milestone.project_id
    try:
        db.add(item)
        db.flush()
        record_event(db, project_id=milestone.project_id, event_type="payment_recorded", object_type="payment", object_id=item.id, title=f"已记录付款：{milestone.name}", detail=f"收款方 {item.payee}" + (f"；强制记录原因：{body.override_reason}" if body.override_reason else ""), amount_delta_cents=item.amount_cents, actor=user.name)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = db.scalar(select(PaymentRecord).where(
            PaymentRecord.project_id == project_id,
            PaymentRecord.idempotency_key == body.idempotency_key,
        ))
        if existing and existing.request_fingerprint == fingerprint and existing.record_type == "normal":
            response.status_code = 200
            return {
                "id": existing.id,
                "controlled": existing.controlled,
                "message": "该笔付款已记录",
                "replayed": True,
            }
        if existing:
            raise HTTPException(409, {
                "code": "IDEMPOTENCY_KEY_REUSED",
                "message": "同一请求标识不能用于不同的付款内容",
            }) from exc
        raise HTTPException(409, {
            "code": "PAYMENT_CONFLICT",
            "message": "付款数据已被其他请求更新，请刷新后重试",
        }) from exc
    return {"id": item.id, "controlled": item.controlled, "message": "已记录付款", "replayed": False}


@app.post("/api/v1/payments/{payment_id}/reverse", status_code=201)
def reverse_payment(payment_id: str, response: Response, reason: str = Form(..., min_length=2, max_length=1000), idempotency_key: str = Form(..., min_length=16, max_length=80), user: User = Depends(current_user), db: Session = Depends(get_db)):
    initial = db.get(PaymentRecord, payment_id)
    if not initial:
        raise HTTPException(404, {"code": "PAYMENT_NOT_FOUND", "message": "原付款记录不存在"})
    require_access(db, initial.project_id, user, write=True)
    lock_financial_project(db, initial.project_id)
    original = db.scalar(
        select(PaymentRecord).where(PaymentRecord.id == payment_id).with_for_update()
    )
    if not original:
        raise HTTPException(404, {"code": "PAYMENT_NOT_FOUND", "message": "原付款记录不存在"})
    if original.record_type != "normal":
        raise HTTPException(409, {
            "code": "PAYMENT_NOT_REVERSIBLE",
            "message": "冲正流水不能再次冲正",
        })
    reason = reason.strip()
    if len(reason) < 2:
        raise HTTPException(422, {"code": "REVERSAL_REASON_REQUIRED", "message": "请填写至少 2 个字符的冲正原因"})
    fingerprint = reverse_payment_fingerprint(original, reason)
    existing_key = db.scalar(select(PaymentRecord).where(
        PaymentRecord.project_id == original.project_id,
        PaymentRecord.idempotency_key == idempotency_key,
    ))
    if existing_key:
        if (
            existing_key.record_type == "reversal"
            and existing_key.reversal_of_payment_id == original.id
            and existing_key.request_fingerprint == fingerprint
        ):
            response.status_code = 200
            return {"id": existing_key.id, "replayed": True}
        raise HTTPException(409, {
            "code": "IDEMPOTENCY_KEY_REUSED",
            "message": "同一请求标识不能用于不同的财务操作",
        })
    existing_reversal = db.scalar(select(PaymentRecord).where(
        PaymentRecord.reversal_of_payment_id == original.id,
    ))
    if existing_reversal:
        raise HTTPException(409, {
            "code": "PAYMENT_ALREADY_REVERSED",
            "message": "该付款已经冲正，不能重复冲正",
            "reversal_id": existing_reversal.id,
        })
    item = PaymentRecord(project_id=original.project_id, milestone_id=original.milestone_id, amount_cents=original.amount_cents, paid_on=date.today(), payee=original.payee, method=original.method, reference=f"冲正 {original.id}", record_type="reversal", controlled=False, override_reason=reason, idempotency_key=idempotency_key, request_fingerprint=fingerprint, reversal_of_payment_id=original.id)
    project_id = original.project_id
    try:
        db.add(item)
        db.flush()
        record_event(db, project_id=original.project_id, event_type="payment_reversed", object_type="payment", object_id=item.id, title="付款已冲正", detail=reason, amount_delta_cents=-item.amount_cents, actor=user.name)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing_key = db.scalar(select(PaymentRecord).where(
            PaymentRecord.project_id == project_id,
            PaymentRecord.idempotency_key == idempotency_key,
        ))
        if existing_key:
            if (
                existing_key.record_type == "reversal"
                and existing_key.reversal_of_payment_id == payment_id
                and existing_key.request_fingerprint == fingerprint
            ):
                response.status_code = 200
                return {"id": existing_key.id, "replayed": True}
            raise HTTPException(409, {
                "code": "IDEMPOTENCY_KEY_REUSED",
                "message": "同一请求标识不能用于不同的财务操作",
            }) from exc
        existing_reversal = db.scalar(select(PaymentRecord).where(
            PaymentRecord.reversal_of_payment_id == payment_id,
        ))
        if existing_reversal:
            raise HTTPException(409, {
                "code": "PAYMENT_ALREADY_REVERSED",
                "message": "该付款已经冲正，不能重复冲正",
                "reversal_id": existing_reversal.id,
            }) from exc
        raise HTTPException(409, {
            "code": "PAYMENT_CONFLICT",
            "message": "付款数据已被其他请求更新，请刷新后重试",
        }) from exc
    return {"id": item.id, "replayed": False}


ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".pdf", ".xlsx", ".docx", ".csv"}


@app.post("/api/v1/projects/{project_id}/evidence", status_code=201)
async def upload_evidence(project_id: str, file: UploadFile = File(...), evidence_type: str = Form("其他"), description: str = Form(""), related_type: str | None = Form(None), related_id: str | None = Form(None), user: User = Depends(current_user), db: Session = Depends(get_db)):
    ensure_file_uploads_enabled()
    require_access(db, project_id, user, write=True)
    original_name = normalize_upload_filename(file.filename, "未命名文件")
    normalized_evidence_type = evidence_type.strip()
    if not normalized_evidence_type or len(normalized_evidence_type) > 60 or len(description) > 400:
        raise HTTPException(422, {
            "code": "INVALID_EVIDENCE_METADATA",
            "message": "证据类型或说明不符合长度要求",
        })
    normalized_related_type, normalized_related_id = validate_evidence_relation(
        db,
        project_id,
        related_type,
        related_id,
    )
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, {"code": "FILE_TYPE_NOT_ALLOWED", "message": "不支持此文件格式"})
    content = await file.read(settings.upload_max_file_bytes + 1)
    inspection = inspect_api_upload(content, suffix, ALLOWED_EXTENSIONS)
    reserve_source_quota(db, project_id, inspection.size_bytes)
    object_key = f"{uuid4().hex}{suffix}"
    full_object_key = f"{project_id}/{object_key}"
    storage = get_source_storage()
    receipt = None
    try:
        receipt = storage.store_bytes(
            full_object_key,
            inspection.content,
            inspection.detected_mime_type,
            {
                "sha256": inspection.sha256,
                "scan-status": source_scan_status(inspection.scan_status),
                "detected-type": inspection.detected_type,
            },
        )
        if not storage.verify(full_object_key, inspection.size_bytes, inspection.sha256):
            raise RuntimeError("证据文件写入后完整性校验失败")
        item = Evidence(
            project_id=project_id,
            original_name=original_name,
            object_key=object_key,
            mime_type=inspection.detected_mime_type,
            size_bytes=inspection.size_bytes,
            sha256=inspection.sha256,
            scan_status=source_scan_status(inspection.scan_status),
            scanned_at=utc_now(),
            evidence_type=normalized_evidence_type,
            description=description,
            related_type=normalized_related_type,
            related_id=normalized_related_id,
        )
        db.add(item)
        db.flush()
        record_event(db, project_id=project_id, event_type="evidence_uploaded", object_type="evidence", object_id=item.id, title=f"上传证据：{item.original_name}", detail=description, actor=user.name)
        db.commit()
    except Exception as exc:
        db.rollback()
        if receipt is not None:
            delete_source_best_effort(storage, full_object_key, receipt.version_id)
        raise HTTPException(503, {
            "code": "SOURCE_FILE_PERSIST_FAILED",
            "message": "文件保存失败，未产生证据记录，请稍后重试",
        }) from exc
    return {
        "id": item.id,
        "original_name": item.original_name,
        "size_bytes": item.size_bytes,
        "sha256": item.sha256,
        "scan_status": item.scan_status,
    }


@app.get("/api/v1/projects/{project_id}/evidence")
def list_evidence(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_access(db, project_id, user)
    items = db.scalars(select(Evidence).where(Evidence.project_id == project_id).order_by(Evidence.created_at.desc())).all()
    return [{"id": item.id, "original_name": item.original_name, "mime_type": item.mime_type, "size_bytes": item.size_bytes, "sha256": item.sha256, "scan_status": item.scan_status, "evidence_type": item.evidence_type, "description": item.description, "related_type": item.related_type, "related_id": item.related_id, "created_at": item.created_at} for item in items]


@app.get("/api/v1/evidence/{evidence_id}/download")
def download_evidence(evidence_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.get(Evidence, evidence_id)
    if not item:
        raise HTTPException(404, {"code": "EVIDENCE_NOT_FOUND", "message": "证据文件不存在"})
    require_access(db, item.project_id, user)
    ensure_source_download_allowed(item.scan_status)
    storage = get_source_storage()
    object_key = f"{item.project_id}/{item.object_key}"
    download_url = storage.download_url(object_key, item.original_name, item.mime_type)
    if download_url:
        return RedirectResponse(download_url, status_code=307)
    local_path = storage.ensure_local(object_key)
    if not local_path:
        raise HTTPException(404, {"code": "EVIDENCE_FILE_MISSING", "message": "证据原文件已丢失，请联系项目所有者"})
    return FileResponse(local_path, media_type=item.mime_type, filename=item.original_name)


@app.get("/api/v1/projects/{project_id}/timeline")
def timeline(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_access(db, project_id, user)
    events = db.scalars(select(AuditEvent).where(AuditEvent.project_id == project_id).order_by(AuditEvent.created_at.desc())).all()
    return [{"id": item.id, "event_type": item.event_type, "title": item.title, "detail": item.detail, "actor": item.actor, "amount_delta_cents": item.amount_delta_cents, "created_at": item.created_at} for item in events]


@app.post("/api/v1/projects/{project_id}/export-jobs", status_code=202)
def create_project_export_job(project_id: str, body: ProjectExportCreate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_recent_login(request)
    project, _ = require_access(db, project_id, user, owner=True)
    if project.status in {"待删除", "删除中"}:
        raise HTTPException(409, {"code": "PROJECT_DELETION_IN_PROGRESS", "message": "项目正在删除，不能生成新档案"})
    pending = db.scalar(select(ProjectExportJob).where(
        ProjectExportJob.project_id == project_id,
        ProjectExportJob.requested_by_user_id == user.id,
        ProjectExportJob.status.in_({"queued", "running"}),
    ))
    if pending:
        raise HTTPException(409, {"code": "EXPORT_ALREADY_RUNNING", "message": "已有档案正在生成，请稍后查看"})
    job = ProjectExportJob(
        project_id=project.id, requested_by_user_id=user.id,
        include_attachments=body.include_attachments,
        date_from=body.date_from, date_to=body.date_to,
        max_attempts=settings.export_job_max_attempts,
    )
    db.add(job)
    db.flush()
    record_event(
        db, project_id=project.id, event_type="project_export_requested",
        object_type="export", object_id=job.id, title="开始生成项目档案",
        detail=f"{'包含附件' if body.include_attachments else '不含附件'}",
        actor=user.name,
    )
    db.commit()
    submit_export_job(job.id)
    return export_job_payload(job)


@app.get("/api/v1/projects/{project_id}/export-jobs")
def list_project_export_jobs(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_access(db, project_id, user, owner=True)
    jobs = db.scalars(select(ProjectExportJob).where(
        ProjectExportJob.project_id == project_id,
    ).order_by(ProjectExportJob.created_at.desc())).all()
    return [export_job_payload(job) for job in jobs]


def require_export_job(db: Session, job_id: str, user: User) -> ProjectExportJob:
    job = db.get(ProjectExportJob, job_id)
    if not job:
        raise HTTPException(404, {"code": "EXPORT_NOT_FOUND", "message": "导出任务不存在"})
    require_access(db, job.project_id, user, owner=True)
    return job


@app.get("/api/v1/project-export-jobs/{job_id}")
def get_project_export_job(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return export_job_payload(require_export_job(db, job_id, user))


@app.post("/api/v1/project-export-jobs/{job_id}/retry", status_code=202)
def retry_project_export_job(job_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_recent_login(request)
    job = require_export_job(db, job_id, user)
    expires_at = aware(job.expires_at) if job.expires_at else None
    if job.status not in {"failed", "dead_letter", "expired"} and not (job.status == "succeeded" and expires_at and expires_at <= utc_now()):
        raise HTTPException(409, {"code": "EXPORT_NOT_RETRYABLE", "message": "当前任务不需要重试"})
    delete_export_artifacts(db, job)
    job.status = "queued"
    job.progress = 5
    job.stage = "等待重新生成"
    job.error_message = None
    job.object_key = None
    job.file_size_bytes = None
    job.artifact_sha256 = None
    job.report_page_count = None
    job.part_count = 0
    job.attempt_count = 0
    job.max_attempts = settings.export_job_max_attempts
    job.lease_owner = None
    job.lease_expires_at = None
    job.next_attempt_at = None
    job.expires_at = None
    job.started_at = None
    job.finished_at = None
    db.commit()
    submit_export_job(job.id)
    return export_job_payload(job)


def require_downloadable_export(job: ProjectExportJob, db: Session) -> None:
    expires_at = aware(job.expires_at) if job.expires_at else None
    if job.status != "succeeded" or not expires_at or expires_at <= utc_now():
        if job.status == "succeeded":
            delete_export_artifacts(db, job)
            job.status = "expired"
            job.stage = "下载链接已过期"
            db.commit()
        raise HTTPException(410, {"code": "EXPORT_EXPIRED", "message": "下载链接已过期，请重新生成"})


def export_artifact_response(artifact: ProjectExportArtifact):
    if not verify_export_artifact(artifact):
        raise HTTPException(409, {"code": "EXPORT_INTEGRITY_FAILED", "message": "档案完整性校验失败，请重新生成"})
    path = export_artifact_path(artifact)
    if not path or not path.is_file():
        url = export_artifact_download_url(artifact)
        if url:
            return RedirectResponse(url, status_code=307)
        raise HTTPException(410, {"code": "EXPORT_FILE_MISSING", "message": "档案文件已失效，请重新生成"})
    return FileResponse(path, media_type="application/zip", filename=artifact.filename)


def legacy_export_artifact_response(job: ProjectExportJob):
    if not verify_export_artifact(job):
        raise HTTPException(409, {"code": "EXPORT_INTEGRITY_FAILED", "message": "档案完整性校验失败，请重新生成"})
    path = export_artifact_path(job)
    if path and path.is_file():
        return FileResponse(path, media_type="application/zip", filename="项目档案-主卷.zip")
    url = export_artifact_download_url(job, "项目档案-主卷.zip")
    if url:
        return RedirectResponse(url, status_code=307)
    raise HTTPException(410, {"code": "EXPORT_FILE_MISSING", "message": "档案文件已失效，请重新生成"})


@app.get("/api/v1/project-export-jobs/{job_id}/download")
def download_project_export(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = require_export_job(db, job_id, user)
    require_downloadable_export(job, db)
    artifact = db.scalar(select(ProjectExportArtifact).where(
        ProjectExportArtifact.job_id == job.id,
        ProjectExportArtifact.part_number == 1,
    ))
    if not artifact:
        return legacy_export_artifact_response(job)
    return export_artifact_response(artifact)


@app.get("/api/v1/project-export-jobs/{job_id}/artifacts/{artifact_id}/download")
def download_project_export_artifact(job_id: str, artifact_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = require_export_job(db, job_id, user)
    require_downloadable_export(job, db)
    if artifact_id == "legacy" and job.object_key:
        return legacy_export_artifact_response(job)
    artifact = db.get(ProjectExportArtifact, artifact_id)
    if not artifact or artifact.job_id != job.id:
        raise HTTPException(404, {"code": "EXPORT_ARTIFACT_NOT_FOUND", "message": "档案分卷不存在"})
    return export_artifact_response(artifact)
