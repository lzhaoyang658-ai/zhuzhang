from datetime import date, datetime, timedelta, timezone
import io
import zipfile

import pytest
from fastapi import HTTPException
from pypdf import PdfReader
from sqlalchemy import select

from app.models import (AuditEvent, BaselineVersion, ChangeOrder, DeletedProjectRecord, Evidence, LoginChallenge,
                        LoginSession, Notification, PaymentMilestone, Project, ProjectExportJob,
                        ProjectBudgetCategory, ProjectFundLimitHistory,
                        PaymentRecord, ProjectMembership, QuoteCorrection, QuoteParseJob)
from app.services.quote_jobs import process_quote_job_in_session
from app.services.project_lifecycle import purge_due_projects
from app.services.notification_digest import send_due_notification_digests
from app.services.export_jobs import claim_next_export_job, process_export_job_in_session
from app.core.config import get_settings
from app.main import ensure_source_download_allowed


def make_project(db_session):
    project = Project(name="API 测试", city="苏州", area_sqm=100, fund_limit_cents=400_000_00)
    db_session.add(project)
    db_session.flush()
    db_session.add(ProjectMembership(user_id="demo-owner", project_id=project.id, role="owner"))
    db_session.add(BaselineVersion(project_id=project.id, version=1, amount_cents=280_000_00))
    milestone = PaymentMilestone(project_id=project.id, name="水电款", planned_amount_cents=80_000_00, planned_date=date.today(), condition="水电验收")
    db_session.add(milestone)
    db_session.commit()
    return project, milestone


def test_liveness_readiness_and_authenticated_task_health(client):
    live = client.get("/health")
    assert live.json()["status"] == "ok"
    assert live.headers["cache-control"] == "no-store"
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.headers["cache-control"] == "no-store"
    assert ready.json()["database"] == "available"
    assert ready.json()["sqlite_foreign_keys"] is True
    assert ready.json()["malware_scanner"] == {"mode": "disabled", "status": "not_applicable"}
    task_health = client.get("/api/v1/task-health")
    assert task_health.status_code == 200
    assert {"quote", "export", "checked_at"}.issubset(task_health.json())


def test_readiness_requires_clamav_when_scanning_is_enabled(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_malware_scan_mode", "clamav")
    monkeypatch.setattr(settings, "clamav_host", "clamd.internal")
    monkeypatch.setattr(settings, "clamav_readiness_timeout_seconds", 1.25)
    probe_arguments = {}

    def healthy_probe(**kwargs):
        probe_arguments.update(kwargs)
        return "healthy"

    monkeypatch.setattr("app.main.check_clamav_readiness", healthy_probe)

    healthy = client.get("/health/ready")

    assert healthy.status_code == 200
    assert healthy.json()["malware_scanner"] == {"mode": "clamav", "status": "healthy"}
    assert probe_arguments == {"host": "clamd.internal", "port": 3310, "timeout_seconds": 1.25}

    monkeypatch.setattr("app.main.check_clamav_readiness", lambda **_kwargs: "unavailable")
    unavailable = client.get("/health/ready")

    assert unavailable.status_code == 503
    assert unavailable.json()["status"] == "degraded"
    assert unavailable.json()["malware_scanner"] == {"mode": "clamav", "status": "unavailable"}
    assert client.get("/health").status_code == 200


def test_disabled_uploads_make_scanner_not_applicable(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "uploads_enabled", False)
    monkeypatch.setattr(settings, "upload_malware_scan_mode", "clamav")
    monkeypatch.setattr(
        "app.main.check_clamav_readiness",
        lambda **_kwargs: pytest.fail("disabled uploads must not probe ClamAV"),
    )

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["malware_scanner"] == {"mode": "disabled", "status": "not_applicable"}


def test_production_readiness_hides_internal_diagnostics(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded"}


def test_maintenance_mode_blocks_writes_but_keeps_reads(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "maintenance_mode", True)
    blocked = client.post("/api/v1/projects", json={})

    assert blocked.status_code == 503
    assert blocked.headers["retry-after"] == "120"
    assert blocked.json()["error"]["code"] == "MAINTENANCE_MODE"
    assert client.get("/health").status_code == 200


def test_production_never_returns_a_development_login_code(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "app_env", "production")
    monkeypatch.setattr(get_settings(), "auth_delivery_mode", "development")

    response = client.post("/api/v1/auth/email/request-code", json={"email": "person@example.com"})

    assert response.status_code == 202
    assert "development_code" not in response.json()


def import_and_finish_quote(client, db_session, project_id, filename, content):
    response = client.post(
        f"/api/v1/projects/{project_id}/quotes/import",
        files={"file": (filename, content, "text/csv")},
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "queued"
    process_quote_job_in_session(db_session, payload["job"]["id"])
    return payload["id"]


def test_disabled_uploads_are_rejected_before_inspection_or_storage(client, db_session, monkeypatch):
    project, _ = make_project(db_session)
    monkeypatch.setattr(get_settings(), "uploads_enabled", False)
    monkeypatch.setattr(
        "app.main.inspect_api_upload",
        lambda *_args, **_kwargs: pytest.fail("disabled uploads must not inspect file content"),
    )
    monkeypatch.setattr(
        "app.main.get_source_storage",
        lambda: pytest.fail("disabled uploads must not initialize storage"),
    )

    quote = client.post(
        f"/api/v1/projects/{project.id}/quotes/import",
        files={"file": ("quote.csv", b"name,amount\nitem,1\n", "text/csv")},
    )
    evidence = client.post(
        f"/api/v1/projects/{project.id}/evidence",
        files={"file": ("proof.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
    )

    for response in (quote, evidence):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "FILE_UPLOADS_DISABLED"
    assert db_session.query(QuoteParseJob).count() == 0
    assert db_session.query(Evidence).count() == 0


def test_session_exposes_upload_capability(client, monkeypatch):
    settings = get_settings()
    assert client.get("/api/v1/session").json()["capabilities"]["uploads_enabled"] is True

    monkeypatch.setattr(settings, "uploads_enabled", False)

    assert client.get("/api/v1/session").json()["capabilities"]["uploads_enabled"] is False


def test_change_state_machine_updates_budget(client, db_session):
    project, _ = make_project(db_session)
    response = client.post(f"/api/v1/projects/{project.id}/changes", json={
        "change_type": "increase", "title": "新增插座", "reason": "使用需要",
        "content": "增加五孔插座两个", "amount_cents": 800_00,
        "proposed_on": date.today().isoformat(), "no_attachment_acknowledged": True,
    })
    assert response.status_code == 201
    change_id = response.json()["id"]
    assert client.post(f"/api/v1/changes/{change_id}/actions/send", json={}).status_code == 200
    pending = client.get(f"/api/v1/projects/{project.id}/dashboard").json()["budget"]
    assert pending["pending_risk_cents"] == 800_00
    assert client.post(f"/api/v1/changes/{change_id}/actions/approve", json={"comment": "同意"}).status_code == 200
    approved = client.get(f"/api/v1/projects/{project.id}/dashboard").json()["budget"]
    assert approved["pending_risk_cents"] == 0
    assert approved["approved_budget_cents"] == 280_800_00


def test_change_requires_explicit_no_attachment_acknowledgement(client, db_session):
    project, _ = make_project(db_session)
    payload = {
        "change_type": "increase",
        "title": "现场新增项",
        "reason": "现场调整",
        "content": "增加一项施工内容",
        "amount_cents": 100_00,
        "proposed_on": date.today().isoformat(),
    }

    assert client.post(f"/api/v1/projects/{project.id}/changes", json=payload).status_code == 422
    assert client.post(
        f"/api/v1/projects/{project.id}/changes",
        json={**payload, "no_attachment_acknowledged": False},
    ).status_code == 422
    accepted = client.post(
        f"/api/v1/projects/{project.id}/changes",
        json={**payload, "no_attachment_acknowledged": True},
    )
    assert accepted.status_code == 201


def test_high_risk_payment_requires_override_reason(client, db_session):
    project, milestone = make_project(db_session)
    payload = {
        "amount_cents": 10_000_00,
        "paid_on": date.today().isoformat(),
        "payee": "施工方",
        "idempotency_key": "high-risk-payment-0001",
    }
    denied = client.post(f"/api/v1/milestones/{milestone.id}/payments", json=payload)
    assert denied.status_code == 409
    payload["override_reason"] = "已发生付款，补录事实"
    recorded = client.post(f"/api/v1/milestones/{milestone.id}/payments", json=payload)
    assert recorded.status_code == 201
    assert recorded.json()["controlled"] is False


def test_payment_none_acceptance_idempotency_and_overpay_guard(client, db_session):
    project, milestone = make_project(db_session)
    milestone.required_acceptance = "无"
    db_session.commit()

    check = client.get(f"/api/v1/milestones/{milestone.id}/payment-check")
    assert check.status_code == 200
    assert check.json()["result"] == "ready"
    assert all(item["ok"] for item in check.json()["checks"][:2])

    overpaid = client.post(f"/api/v1/milestones/{milestone.id}/payments", json={
        "amount_cents": 80_000_01,
        "paid_on": date.today().isoformat(),
        "payee": "施工方",
        "idempotency_key": "payment-over-limit",
    })
    assert overpaid.status_code == 409
    assert overpaid.json()["error"]["code"] == "PAYMENT_EXCEEDS_REMAINING"

    payload = {
        "amount_cents": 80_000_00,
        "paid_on": date.today().isoformat(),
        "payee": "施工方",
        "method": "银行转账",
        "reference": "TEST-001",
        "idempotency_key": "payment-exact-once",
    }
    first = client.post(f"/api/v1/milestones/{milestone.id}/payments", json=payload)
    repeated = client.post(f"/api/v1/milestones/{milestone.id}/payments", json=payload)
    assert first.status_code == 201 and repeated.status_code == 200
    assert repeated.json()["replayed"] is True
    assert repeated.json()["id"] == first.json()["id"]
    assert db_session.query(PaymentRecord).filter(PaymentRecord.milestone_id == milestone.id).count() == 1

    after_paid = client.post(f"/api/v1/milestones/{milestone.id}/payments", json={
        **payload,
        "amount_cents": 1,
        "idempotency_key": "payment-after-paid",
    })
    assert after_paid.status_code == 409
    assert after_paid.json()["error"]["code"] == "MILESTONE_ALREADY_PAID"

    listed = client.get(f"/api/v1/projects/{project.id}/payments")
    assert listed.status_code == 200
    assert listed.json()[0]["milestone_name"] == milestone.name
    assert listed.json()[0]["reference"] == "TEST-001"


def test_evidence_list_exposes_relation_and_downloads_original(client, db_session, monkeypatch, tmp_path):
    project, milestone = make_project(db_session)
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path / "uploads")
    content = b"%PDF-1.4\nrenovation-proof\n%%EOF"

    uploaded = client.post(
        f"/api/v1/projects/{project.id}/evidence",
        files={"file": ("水电验收.pdf", content, "application/pdf")},
        data={
            "evidence_type": "验收记录",
            "description": "水电节点现场签字件",
            "related_type": "milestone",
            "related_id": milestone.id,
        },
    )
    assert uploaded.status_code == 201
    evidence_id = uploaded.json()["id"]

    listed = client.get(f"/api/v1/projects/{project.id}/evidence")
    assert listed.status_code == 200
    assert listed.json()[0]["related_type"] == "milestone"
    assert listed.json()[0]["related_id"] == milestone.id
    assert listed.json()[0]["mime_type"] == "application/pdf"
    assert len(listed.json()[0]["sha256"]) == 64
    assert listed.json()[0]["scan_status"] == "skipped"
    db_session.refresh(project)
    assert project.source_file_count == 1
    assert project.source_bytes == len(content)

    downloaded = client.get(f"/api/v1/evidence/{evidence_id}/download")
    assert downloaded.status_code == 200
    assert downloaded.content == content
    assert downloaded.headers["content-type"].startswith("application/pdf")


def test_evidence_rejects_spoofed_content_and_cross_project_relation(client, db_session, monkeypatch, tmp_path):
    project, _ = make_project(db_session)
    other_project, other_milestone = make_project(db_session)
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path / "uploads")

    spoofed = client.post(
        f"/api/v1/projects/{project.id}/evidence",
        files={"file": ("伪装.pdf", b"this is not a pdf", "application/pdf")},
    )
    assert spoofed.status_code == 415
    assert spoofed.json()["error"]["code"] == "FILE_CONTENT_MISMATCH"

    cross_project = client.post(
        f"/api/v1/projects/{project.id}/evidence",
        files={"file": ("验收.pdf", b"%PDF-1.4\nproof\n%%EOF", "application/pdf")},
        data={"related_type": "milestone", "related_id": other_milestone.id},
    )
    assert cross_project.status_code == 404
    assert cross_project.json()["error"]["code"] == "EVIDENCE_RELATION_NOT_FOUND"
    assert db_session.query(Evidence).filter(Evidence.project_id == project.id).count() == 0
    db_session.refresh(project)
    assert project.source_file_count == 0 and project.source_bytes == 0
    assert other_project.id != project.id


def test_evidence_storage_verification_failure_is_compensated(client, db_session, monkeypatch, tmp_path):
    project, _ = make_project(db_session)
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(get_settings(), "upload_dir", upload_dir)
    monkeypatch.setattr("app.services.source_storage.LocalSourceStorage.verify", lambda *_args: False)

    failed = client.post(
        f"/api/v1/projects/{project.id}/evidence",
        files={"file": ("现场.pdf", b"%PDF-1.4\nproof\n%%EOF", "application/pdf")},
    )
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "SOURCE_FILE_PERSIST_FAILED"
    assert db_session.query(Evidence).filter(Evidence.project_id == project.id).count() == 0
    db_session.refresh(project)
    assert project.source_file_count == 0 and project.source_bytes == 0
    assert not list(upload_dir.rglob("*")) if upload_dir.exists() else True


def test_project_file_quota_rejects_before_storage_write(client, db_session, monkeypatch, tmp_path):
    project, _ = make_project(db_session)
    project.source_file_count = get_settings().upload_project_max_files
    db_session.commit()
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(get_settings(), "upload_dir", upload_dir)

    rejected = client.post(
        f"/api/v1/projects/{project.id}/evidence",
        files={"file": ("现场.pdf", b"%PDF-1.4\nproof\n%%EOF", "application/pdf")},
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "PROJECT_FILE_QUOTA_EXCEEDED"
    assert not upload_dir.exists()


def test_production_blocks_legacy_unscanned_source_download(monkeypatch):
    monkeypatch.setattr(get_settings(), "app_env", "production")
    with pytest.raises(HTTPException) as raised:
        ensure_source_download_allowed("legacy_unscanned")
    assert raised.value.status_code == 423
    assert raised.value.detail["code"] == "SOURCE_FILE_NOT_CLEARED"


def test_external_confirmation_is_idempotent(client, db_session):
    project, _ = make_project(db_session)
    created = client.post(f"/api/v1/projects/{project.id}/changes", json={
        "change_type": "increase", "title": "墙面找平", "reason": "现场调整",
        "content": "增加找平施工", "amount_cents": 1800_00,
        "proposed_on": date.today().isoformat(), "no_attachment_acknowledged": True,
    }).json()
    token = client.post(f"/api/v1/changes/{created['id']}/share").json()["token"]
    decision = {"decision": "approve", "version": created["version"], "name": "王工", "role": "项目经理", "comment": "同意", "current_version_confirmed": True}
    missing_version = client.post(
        f"/api/v1/external/changes/{token}/decision",
        json={key: value for key, value in decision.items() if key != "version"},
    )
    assert missing_version.status_code == 422
    first = client.post(f"/api/v1/external/changes/{token}/decision", json=decision)
    second = client.post(f"/api/v1/external/changes/{token}/decision", json=decision)
    assert first.status_code == 200
    assert second.status_code == 409
    events = db_session.scalars(select(AuditEvent).where(
        AuditEvent.project_id == project.id,
        AuditEvent.event_type == "change_approve",
    )).all()
    assert len(events) == 1
    assert events[0].actor == "外部自报"


def test_external_confirmation_preserves_decrease_direction(client, db_session):
    project, _ = make_project(db_session)
    created = client.post(f"/api/v1/projects/{project.id}/changes", json={
        "change_type": "decrease", "title": "取消壁龛", "reason": "墙体厚度不足",
        "content": "取消客卫壁龛施工", "amount_cents": 1200_00,
        "proposed_on": date.today().isoformat(), "no_attachment_acknowledged": True,
    }).json()
    token = client.post(f"/api/v1/changes/{created['id']}/share").json()["token"]

    payload = client.get(f"/api/v1/external/changes/{token}").json()
    assert payload["project_name"] == "装修项目"
    assert payload["change"]["change_type"] == "decrease"
    assert payload["change"]["amount_cents"] == 1200_00


def test_csv_quote_can_be_confirmed_and_activated(client, db_session):
    project, _ = make_project(db_session)
    csv_content = "项目名称,区域,类别,数量,单位,单价,合价\n墙面找平,厨房,泥瓦,18,㎡,120,2160\n".encode("utf-8")
    quote_id = import_and_finish_quote(client, db_session, project.id, "报价.csv", csv_content)
    db_session.refresh(project)
    assert project.source_file_count == 1
    assert project.source_bytes == len(csv_content)
    detail = client.get(f"/api/v1/quotes/{quote_id}").json()
    assert detail["source_mime_type"] == "text/csv; charset=utf-8"
    assert len(detail["source_sha256"]) == 64
    assert detail["scan_status"] == "skipped"
    assert detail["total_cents"] == 2160_00
    assert client.post(f"/api/v1/quotes/{quote_id}/confirm").status_code == 200
    activated = client.post(f"/api/v1/quotes/{quote_id}/activate-baseline")
    assert activated.status_code == 201
    assert activated.json()["version"] == 2


def test_quote_item_correction_is_logged_and_total_recalculated(client, db_session):
    project, _ = make_project(db_session)
    csv_content = "项目名称,区域,类别,数量,单位,单价,合价\n墙面找平,厨房,泥瓦,18,㎡,120,2160\n".encode("utf-8")
    quote_id = import_and_finish_quote(client, db_session, project.id, "报价.csv", csv_content)
    detail = client.get(f"/api/v1/quotes/{quote_id}").json()
    item_id = detail["items"][0]["id"]
    updated = client.patch(
        f"/api/v1/quote-items/{item_id}",
        json={"quantity": "20", "unit_price_cents": 125_00, "actor": "测试用户"},
    )
    assert updated.status_code == 200
    assert updated.json()["total_cents"] == 2500_00
    refreshed = client.get(f"/api/v1/quotes/{quote_id}").json()
    assert refreshed["correction_count"] == 3
    assert refreshed["total_cents"] == 2500_00
    assert {row.actor for row in db_session.scalars(select(QuoteCorrection).where(QuoteCorrection.quote_id == quote_id))} == {"林然", "系统重算"}
    event = db_session.scalar(select(AuditEvent).where(AuditEvent.object_id == item_id).order_by(AuditEvent.created_at.desc()))
    assert event.actor == "林然"


def test_failed_quote_job_can_be_requeued(client, db_session, monkeypatch):
    project, _ = make_project(db_session)
    csv_content = "项目名称,数量,单位,单价,合价\n墙面找平,18,㎡,120,2160\n".encode("utf-8")
    imported = client.post(
        f"/api/v1/projects/{project.id}/quotes/import",
        files={"file": ("待重试.csv", csv_content, "text/csv")},
    ).json()
    monkeypatch.setattr("app.services.quote_jobs.parse_quote_document", lambda *_args: (_ for _ in ()).throw(ValueError("模拟解析失败")))
    job_id = imported["job"]["id"]
    process_quote_job_in_session(db_session, job_id)
    waiting = client.get(f"/api/v1/quote-jobs/{job_id}").json()
    assert waiting["status"] == "queued" and waiting["attempt"] == 1
    for expected_attempt in (2, 3):
        job = db_session.get(QuoteParseJob, job_id)
        job.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db_session.commit()
        process_quote_job_in_session(db_session, job_id)
        assert db_session.get(QuoteParseJob, job_id).attempt == expected_attempt
    failed = client.get(f"/api/v1/quote-jobs/{job_id}")
    assert failed.json()["status"] == "failed"
    assert db_session.get(QuoteParseJob, job_id).status == "dead_letter"
    retried = client.post(f"/api/v1/quote-jobs/{job_id}/retry")
    assert retried.status_code == 202
    assert retried.json()["status"] == "queued"
    assert retried.json()["attempt"] == 0


def test_three_quote_comparison_matches_synonyms(client, db_session):
    project, _ = make_project(db_session)
    contents = [
        "项目名称,区域,类别,数量,单位,单价,合价\n乳胶漆涂刷,客厅,油漆,10,㎡,50,500\n",
        "项目名称,区域,类别,数量,单位,单价,合价\n墙顶面乳胶漆,客厅,油漆,10,㎡,55,550\n",
        "项目名称,区域,类别,数量,单位,单价,合价\n墙面乳胶漆,客厅,油漆,10,㎡,60,600\n",
    ]
    quote_ids = []
    for index, content in enumerate(contents):
        quote_ids.append(import_and_finish_quote(
            client, db_session, project.id, f"报价{index}.csv", content.encode("utf-8")
        ))
    params = [("quote_ids", quote_id) for quote_id in quote_ids]
    comparison = client.get(f"/api/v1/projects/{project.id}/quotes/compare", params=params)
    assert comparison.status_code == 200
    payload = comparison.json()
    assert payload["summary"]["matched_group_count"] == 1
    assert payload["summary"]["total_spread_cents"] == 100_00
    assert len(payload["groups"][0]["items"]) == 3

    suggested = payload["groups"][0]
    matched = client.post(
        f"/api/v1/projects/{project.id}/quote-match-groups",
        json={"item_ids": [suggested["items"][quote_id]["id"] for quote_id in quote_ids], "canonical_name": "墙面乳胶漆"},
    )
    assert matched.status_code == 201
    confirmed = client.get(f"/api/v1/projects/{project.id}/quotes/compare", params=params).json()
    assert confirmed["groups"][0]["match_type"] == "manual"
    assert confirmed["groups"][0]["match_confidence"] == 100
    assert client.delete(f"/api/v1/quote-match-groups/{matched.json()['id']}").status_code == 204
    reverted = client.get(f"/api/v1/projects/{project.id}/quotes/compare", params=params).json()
    assert reverted["groups"][0]["match_type"] == "suggested"


def test_family_invite_and_permission_isolation(client, db_session):
    project, _ = make_project(db_session)
    invited = client.post(
        f"/api/v1/projects/{project.id}/invites",
        json={"email": "family@example.com", "role": "viewer"},
    )
    assert invited.status_code == 201
    token = invited.json()["token"]
    public_invite = client.get(f"/api/v1/invites/{token}")
    assert public_invite.status_code == 200
    assert public_invite.json()["project_name"] == "装修项目"
    assert public_invite.json()["email"] != "family@example.com"
    assert public_invite.json()["email"].endswith("@example.com")

    client.headers.pop("X-Demo-User-Id", None)
    requested = client.post("/api/v1/auth/email/request-code", json={"email": "family@example.com"})
    code = requested.json()["development_code"]
    verified = client.post("/api/v1/auth/email/verify", json={"email": "family@example.com", "code": code, "name": "家人"})
    assert verified.status_code == 200
    csrf = client.cookies.get("zhuzhang_csrf")
    accepted = client.post(f"/api/v1/invites/{token}/accept", headers={"X-CSRF-Token": csrf}, json={"name": "家人"})
    assert accepted.status_code == 200
    member_user_id = accepted.json()["user"]["id"]
    client.headers["X-Demo-User-Id"] = "demo-owner"
    viewer_headers = {"X-Demo-User-Id": member_user_id}

    assert client.get(f"/api/v1/projects/{project.id}/dashboard", headers=viewer_headers).status_code == 200
    denied = client.post(f"/api/v1/projects/{project.id}/changes", headers=viewer_headers, json={
        "change_type": "increase", "title": "只读越权", "reason": "权限测试", "content": "不应写入",
        "amount_cents": 100_00, "proposed_on": date.today().isoformat(), "no_attachment_acknowledged": True,
    })
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "READ_ONLY_MEMBER"
    assert client.post(f"/api/v1/projects/{project.id}/invites", headers=viewer_headers, json={"email": "other@example.com", "role": "viewer"}).status_code == 403

    foreign_project, _ = make_project(db_session)
    assert client.get(f"/api/v1/projects/{foreign_project.id}/dashboard", headers=viewer_headers).status_code == 404

    members = client.get(f"/api/v1/projects/{project.id}/members").json()["members"]
    membership_id = next(item["id"] for item in members if item["user"]["id"] == member_user_id)
    promoted = client.patch(f"/api/v1/project-memberships/{membership_id}", json={"role": "co_manager"})
    assert promoted.status_code == 200
    assert client.post(f"/api/v1/projects/{project.id}/changes", headers=viewer_headers, json={
        "change_type": "increase", "title": "共同管理者变更", "reason": "权限测试", "content": "允许写入",
        "amount_cents": 100_00, "proposed_on": date.today().isoformat(), "no_attachment_acknowledged": True,
    }).status_code == 201
    assert client.delete(f"/api/v1/project-memberships/{membership_id}").status_code == 204
    assert client.get(f"/api/v1/projects/{project.id}/dashboard", headers=viewer_headers).status_code == 404


def test_email_login_uses_hashed_session_and_csrf(client, db_session):
    client.headers.pop("X-Demo-User-Id", None)
    assert client.get("/api/v1/session").status_code == 401

    requested = client.post("/api/v1/auth/email/request-code", json={"email": "owner@example.local"})
    assert requested.status_code == 202
    code = requested.json()["development_code"]
    challenge = db_session.scalar(select(LoginChallenge).where(LoginChallenge.email == "owner@example.local"))
    assert challenge is not None
    assert challenge.code_hash != code

    wrong = client.post("/api/v1/auth/email/verify", json={"email": "owner@example.local", "code": "000000" if code != "000000" else "999999"})
    assert wrong.status_code == 400
    verified = client.post("/api/v1/auth/email/verify", json={"email": "owner@example.local", "code": code})
    assert verified.status_code == 200
    assert client.get("/api/v1/session").json()["mode"] == "secure_session"

    stored = db_session.scalar(select(LoginSession))
    assert stored is not None
    assert stored.token_hash != client.cookies.get("zhuzhang_session")
    assert client.post("/api/v1/projects", json={"name": "缺少 CSRF", "city": "上海", "area_sqm": 80, "fund_limit_cents": 100_000_00}).status_code == 403

    csrf = client.cookies.get("zhuzhang_csrf")
    created = client.post("/api/v1/projects", headers={"X-CSRF-Token": csrf}, json={"name": "安全会话项目", "city": "上海", "area_sqm": 80, "fund_limit_cents": 100_000_00})
    assert created.status_code == 201
    assert len(client.get("/api/v1/auth/sessions").json()) == 1
    assert client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert client.get("/api/v1/session").status_code == 401


def test_project_onboarding_archive_and_delayed_deletion(client, db_session, tmp_path):
    invalid_reserve = client.post("/api/v1/projects", json={
        "name": "超额预留金", "city": "苏州", "area_sqm": 80,
        "fund_limit_cents": 100_000_00, "reserve_cents": 100_000_01,
    })
    assert invalid_reserve.status_code == 422
    invalid_dates = client.post("/api/v1/projects", json={
        "name": "日期倒置", "city": "苏州", "area_sqm": 80,
        "fund_limit_cents": 100_000_00,
        "planned_start": "2026-09-02", "planned_end": "2026-09-01",
    })
    assert invalid_dates.status_code == 422
    assert invalid_dates.json()["error"]["code"] == "VALIDATION_ERROR"

    created = client.post("/api/v1/projects", json={
        "name": "梧桐路新家", "city": "杭州", "area_sqm": 96,
        "area_basis": "套内面积", "renovation_type": "半包",
        "planned_start": date.today().isoformat(),
        "planned_end": (date.today() + timedelta(days=120)).isoformat(),
        "fund_limit_cents": 360_000_00, "reserve_cents": 30_000_00,
        "address": "梧桐路", "notes": "首套自住装修",
    })
    assert created.status_code == 201
    project_id = created.json()["id"]
    settings = client.get(f"/api/v1/projects/{project_id}/settings").json()
    assert len(settings["categories"]) == 12
    assert len(settings["fund_limit_history"]) == 1
    assert len(client.get(f"/api/v1/projects/{project_id}/milestones").json()) == 5

    missing_reason = client.patch(f"/api/v1/projects/{project_id}", json={"fund_limit_cents": 380_000_00})
    assert missing_reason.status_code == 422
    invalid_reserve_update = client.patch(f"/api/v1/projects/{project_id}", json={"reserve_cents": 360_000_01})
    assert invalid_reserve_update.status_code == 422
    assert invalid_reserve_update.json()["error"]["code"] == "RESERVE_EXCEEDS_FUND_LIMIT"
    updated = client.patch(f"/api/v1/projects/{project_id}", json={"fund_limit_cents": 380_000_00, "fund_limit_reason": "增加家具家电预算"})
    assert updated.status_code == 200
    assert updated.json()["fund_limit_history"][0]["previous_cents"] == 360_000_00

    assert client.post(f"/api/v1/projects/{project_id}/archive").json()["status"] == "已归档"
    blocked = client.post(f"/api/v1/projects/{project_id}/changes", json={
        "change_type": "increase", "title": "归档后写入", "reason": "权限测试",
        "content": "不应写入", "amount_cents": 100_00,
        "proposed_on": date.today().isoformat(), "no_attachment_acknowledged": True,
    })
    assert blocked.status_code == 409
    assert client.post(f"/api/v1/projects/{project_id}/reopen").json()["status"] == "施工中"
    assert client.get("/api/v1/notifications").status_code == 200

    mismatch = client.post(f"/api/v1/projects/{project_id}/deletion-request", json={"project_name": "错误名称"})
    assert mismatch.status_code == 422
    deletion = client.post(f"/api/v1/projects/{project_id}/deletion-request", json={"project_name": "梧桐路新家"})
    assert deletion.status_code == 202
    assert client.post(f"/api/v1/projects/{project_id}/deletion-cancel").json()["status"] == "施工中"
    assert client.post(f"/api/v1/projects/{project_id}/deletion-request", json={"project_name": "梧桐路新家"}).status_code == 202

    project = db_session.get(Project, project_id)
    project.deletion_scheduled_for = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    project_dir = tmp_path / project_id
    project_dir.mkdir()
    (project_dir / "unused.txt").write_text("temporary", encoding="utf-8")
    assert purge_due_projects(db_session, tmp_path) == [project_id]
    assert db_session.get(Project, project_id) is None
    assert db_session.scalar(select(Notification).where(Notification.project_id == project_id)) is None
    assert db_session.scalar(select(DeletedProjectRecord)) is not None
    assert not project_dir.exists()


def test_notification_rules_dedupe_resolution_and_preferences(client, db_session):
    project, milestone = make_project(db_session)
    db_session.add(PaymentMilestone(
        project_id=project.id,
        name="泥木阶段款",
        planned_amount_cents=60_000_00,
        planned_date=date.today() + timedelta(days=3),
        condition="泥木验收完成",
    ))
    project.fund_limit_cents = 300_000_00
    db_session.commit()
    category = db_session.scalar(select(ProjectBudgetCategory).where(
        ProjectBudgetCategory.project_id == project.id,
        ProjectBudgetCategory.name == "水电",
    ))
    if not category:
        category = ProjectBudgetCategory(project_id=project.id, name="水电", planned_limit_cents=500_00, sort_order=1)
        db_session.add(category)
    else:
        category.planned_limit_cents = 500_00
    db_session.commit()

    created = client.post(f"/api/v1/projects/{project.id}/changes", json={
        "change_type": "increase", "title": "新增回路", "reason": "设备增加",
        "content": "厨房新增独立回路", "amount_cents": 800_00,
        "category": "水电", "proposed_on": date.today().isoformat(),
        "no_attachment_acknowledged": True,
    }).json()
    assert client.post(f"/api/v1/changes/{created['id']}/actions/send", json={}).status_code == 200
    settings = client.get(f"/api/v1/projects/{project.id}/settings").json()
    water = next(item for item in settings["categories"] if item["name"] == "水电")
    assert water["forecast_cents"] == 800_00
    change = db_session.get(ChangeOrder, created["id"])
    change.updated_at = datetime.now(timezone.utc) - timedelta(hours=49)
    db_session.commit()

    db_session.add(PaymentRecord(
        project_id=project.id,
        milestone_id=milestone.id,
        amount_cents=90_000_00,
        paid_on=date.today(),
        payee="施工方",
        controlled=False,
        override_reason="历史超节点付款测试数据",
    ))
    db_session.commit()

    first = client.get("/api/v1/notifications").json()
    active_codes = {item["code"] for item in first["items"] if item["status"] == "active"}
    assert {"A1", "A3", "A5", "A6", "A7", "A8"}.issubset(active_codes)
    first_count = db_session.query(Notification).count()
    assert first["summary"]["critical"] >= 1
    assert first["summary"]["unread"] == first["summary"]["active"]

    second = client.get("/api/v1/notifications").json()
    assert db_session.query(Notification).count() == first_count
    notification_id = second["items"][0]["id"]
    assert client.post(f"/api/v1/notifications/{notification_id}/read").status_code == 200
    assert client.post("/api/v1/notifications/actions/read-all").json()["updated"] >= 1
    assert client.get("/api/v1/notifications/unread-count").json()["unread"] == 0

    assert client.post(f"/api/v1/changes/{created['id']}/actions/approve", json={}).status_code == 200
    after_approval = client.get("/api/v1/notifications").json()
    resolved = [item for item in after_approval["items"] if item["code"] in {"A1", "A7"}]
    assert resolved and all(item["status"] == "resolved" for item in resolved)
    assert "A2" in {item["code"] for item in after_approval["items"] if item["status"] == "active"}

    preference = client.patch("/api/v1/notification-preferences", json={
        "email_enabled": False,
        "email_digest_frequency": "daily",
    })
    assert preference.status_code == 200
    assert preference.json()["email_digest_frequency"] == "off"


def test_due_email_digest_uses_active_deduplicated_notifications(client, db_session, monkeypatch):
    project, _ = make_project(db_session)
    assert client.get("/api/v1/notifications").status_code == 200
    assert client.patch("/api/v1/notification-preferences", json={"email_enabled": True, "email_digest_frequency": "daily"}).status_code == 200
    delivered = []
    monkeypatch.setattr("app.services.notification_digest.deliver_digest", lambda email, subject, content: delivered.append((email, subject, content)))
    now = datetime.now(timezone.utc)
    result = send_due_notification_digests(db_session, now)
    assert result["sent"] == 1
    assert delivered[0][0] == "owner@example.local"
    assert "待处理" in delivered[0][1]
    assert project.name in delivered[0][2]
    assert send_due_notification_digests(db_session, now + timedelta(hours=2))["sent"] == 0


def test_async_project_export_includes_manifest_attachment_and_event(client, db_session, monkeypatch, tmp_path):
    project, _ = make_project(db_session)
    task_settings = get_settings()
    monkeypatch.setattr(task_settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(task_settings, "export_dir", tmp_path / "exports")
    source_dir = task_settings.upload_dir / project.id
    source_dir.mkdir(parents=True)
    (source_dir / "proof.txt").write_text("现场复核记录", encoding="utf-8")
    db_session.add(Evidence(
        project_id=project.id, original_name="现场记录.txt", object_key="proof.txt",
        mime_type="text/plain", size_bytes=18, evidence_type="现场照片",
    ))
    db_session.commit()

    created = client.post(f"/api/v1/projects/{project.id}/export-jobs", json={})
    assert created.status_code == 202
    job_id = created.json()["id"]
    process_export_job_in_session(db_session, job_id)
    job = db_session.get(ProjectExportJob, job_id)
    assert job.status == "succeeded"
    assert job.expires_at is not None

    downloaded = client.get(f"/api/v1/project-export-jobs/{job_id}/download")
    assert downloaded.status_code == 200
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        names = set(archive.namelist())
        assert {"项目正式报告.pdf", "预算与记录.csv", "验收与付款.csv", "项目时间线.csv", "附件目录.csv", "导出清单.txt"}.issubset(names)
        assert "证据附件/现场记录.txt" in names
        assert "下载链接" not in archive.read("导出清单.txt").decode("utf-8")
        report = PdfReader(io.BytesIO(archive.read("项目正式报告.pdf")))
        assert len(report.pages) >= 5
        report_text = "".join(page.extract_text() or "" for page in report.pages)
        assert "付款节点与验收问题" in report_text
        assert "项目时间线" in report_text
    assert job.report_page_count and job.report_page_count >= 5
    assert job.report_version == "formal-v2"
    assert job.artifact_sha256 and len(job.artifact_sha256) == 64
    assert job.part_count == 1 and len(job.artifacts) == 1
    assert created.json()["attempt_count"] == 0
    listed = client.get(f"/api/v1/projects/{project.id}/export-jobs").json()[0]
    assert listed["part_count"] == 1
    assert listed["artifacts"][0]["filename"] == "项目档案-主卷.zip"

    event = db_session.scalar(select(Notification).where(
        Notification.project_id == project.id,
        Notification.code == "EXPORT_SUCCEEDED",
    ))
    assert event and event.kind == "event" and event.status == "active"
    assert client.post(f"/api/v1/notifications/{event.id}/read").status_code == 200
    db_session.refresh(event)
    assert event.status == "resolved"

    artifact = task_settings.export_dir / job.artifacts[0].object_key
    artifact.write_bytes(artifact.read_bytes() + b"tampered")
    integrity_failure = client.get(f"/api/v1/project-export-jobs/{job_id}/download")
    assert integrity_failure.status_code == 409
    assert integrity_failure.json()["error"]["code"] == "EXPORT_INTEGRITY_FAILED"
    job.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    expired = client.get(f"/api/v1/project-export-jobs/{job_id}/download")
    assert expired.status_code == 410
    db_session.refresh(job)
    assert job.status == "expired" and job.object_key is None
    assert not artifact.exists()
    retried = client.post(f"/api/v1/project-export-jobs/{job_id}/retry")
    assert retried.status_code == 202
    assert retried.json()["status"] == "queued"


def test_large_export_is_split_into_downloadable_parts(client, db_session, monkeypatch, tmp_path):
    project, _ = make_project(db_session)
    task_settings = get_settings()
    monkeypatch.setattr(task_settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(task_settings, "export_dir", tmp_path / "exports")
    monkeypatch.setattr(task_settings, "export_part_size_mb", 0)
    source_dir = task_settings.upload_dir / project.id
    source_dir.mkdir(parents=True)
    (source_dir / "large-proof.txt").write_text("分卷附件", encoding="utf-8")
    db_session.add(Evidence(
        project_id=project.id, original_name="分卷凭证.txt", object_key="large-proof.txt",
        mime_type="text/plain", size_bytes=12, evidence_type="现场记录",
    ))
    db_session.commit()

    created = client.post(f"/api/v1/projects/{project.id}/export-jobs", json={})
    job_id = created.json()["id"]
    process_export_job_in_session(db_session, job_id)
    job = db_session.get(ProjectExportJob, job_id)
    assert job.status == "succeeded" and job.part_count == 2
    assert [item.kind for item in job.artifacts] == ["primary", "attachments"]

    payload = client.get(f"/api/v1/project-export-jobs/{job_id}").json()
    assert len(payload["artifacts"]) == 2
    primary = client.get(f"/api/v1{payload['artifacts'][0]['download_path']}")
    attachment_part = client.get(f"/api/v1{payload['artifacts'][1]['download_path']}")
    with zipfile.ZipFile(io.BytesIO(primary.content)) as archive:
        assert "项目正式报告.pdf" in archive.namelist()
        assert "证据附件/分卷凭证.txt" not in archive.namelist()
    with zipfile.ZipFile(io.BytesIO(attachment_part.content)) as archive:
        assert "证据附件/分卷凭证.txt" in archive.namelist()
        assert "分卷说明.txt" in archive.namelist()


def test_export_worker_reclaims_expired_lease_and_dead_letters(db_session):
    project, _ = make_project(db_session)
    job = ProjectExportJob(
        project_id=project.id,
        requested_by_user_id="demo-owner",
        max_attempts=2,
    )
    db_session.add(job)
    db_session.commit()
    claimed = claim_next_export_job(db_session, "worker-a")
    assert claimed and claimed.id == job.id and claimed.attempt_count == 1
    claimed.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    reclaimed = claim_next_export_job(db_session, "worker-b")
    assert reclaimed and reclaimed.id == job.id and reclaimed.attempt_count == 2
    reclaimed.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    assert claim_next_export_job(db_session, "worker-c") is None
    db_session.refresh(job)
    assert job.status == "dead_letter" and job.lease_owner is None
