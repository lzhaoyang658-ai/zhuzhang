from datetime import date, timedelta

import pytest
from fastapi import HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.config import get_settings
from app.main import api_documentation_options
from app.models import (
    AuditEvent,
    LoginChallenge,
    LoginSession,
    Project,
    ProjectMembership,
    User,
)
from app.schemas.api import (
    MAX_AMOUNT_CENTS,
    BaselineCreate,
    BudgetCategoryUpdate,
    ChangeCreate,
    MilestoneCreate,
    PaymentCreate,
    ProjectCreate,
    ProjectUpdate,
    QuoteItemUpdate,
)
from app.services import auth as auth_service
from app.services.auth import aware, consume_login_challenge, request_ip, utc_now


def secure_login(client, email: str, name: str | None = None) -> dict:
    client.headers.pop("X-Demo-User-Id", None)
    client.cookies.clear()
    requested = client.post("/api/v1/auth/email/request-code", json={"email": email})
    assert requested.status_code == 202
    verification = {"email": email, "code": requested.json()["development_code"]}
    if name:
        verification["name"] = name
    verified = client.post("/api/v1/auth/email/verify", json=verification)
    assert verified.status_code == 200
    return verified.json()


def create_owner_project(db_session) -> Project:
    project = Project(
        name="安全测试项目",
        city="苏州",
        area_sqm=90,
        fund_limit_cents=300_000_00,
    )
    db_session.add(project)
    db_session.flush()
    db_session.add(ProjectMembership(user_id="demo-owner", project_id=project.id, role="owner"))
    db_session.commit()
    return project


def test_invite_accept_blocks_account_takeover_and_is_single_use(client, db_session):
    project = create_owner_project(db_session)
    victim = User(
        name="原账号用户",
        email="victim@example.com",
        email_verified_at=utc_now(),
    )
    db_session.add(victim)
    db_session.commit()

    invited = client.post(
        f"/api/v1/projects/{project.id}/invites",
        json={"email": victim.email, "role": "viewer"},
    )
    assert invited.status_code == 201
    token = invited.json()["token"]

    public = client.get(f"/api/v1/invites/{token}")
    assert public.status_code == 200
    assert public.json()["email"] != victim.email
    assert public.json()["email"].endswith("@example.com")

    client.headers.pop("X-Demo-User-Id", None)
    client.cookies.clear()
    anonymous = client.post(f"/api/v1/invites/{token}/accept", json={"name": "攻击者"})
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "AUTH_REQUIRED"

    secure_login(client, "attacker@example.com", "攻击者")
    csrf = client.cookies.get("zhuzhang_csrf")
    mismatched = client.post(
        f"/api/v1/invites/{token}/accept",
        headers={"X-CSRF-Token": csrf},
        json={"name": "攻击者"},
    )
    assert mismatched.status_code == 403
    assert mismatched.json()["error"]["code"] == "INVITE_EMAIL_MISMATCH"
    assert db_session.scalar(select(func.count(ProjectMembership.id)).where(ProjectMembership.project_id == project.id)) == 1

    secure_login(client, victim.email)
    csrf = client.cookies.get("zhuzhang_csrf")
    sessions_before = db_session.scalar(select(func.count(LoginSession.id)).where(LoginSession.user_id == victim.id))
    accepted = client.post(
        f"/api/v1/invites/{token}/accept",
        headers={"X-CSRF-Token": csrf},
        json={"name": "不应覆盖原姓名"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["project_id"] == project.id
    assert accepted.json()["role"] == "viewer"
    assert accepted.json()["user"]["id"] == victim.id
    db_session.refresh(victim)
    assert victim.name == "原账号用户"
    assert db_session.scalar(select(func.count(LoginSession.id)).where(LoginSession.user_id == victim.id)) == sessions_before

    repeated = client.post(
        f"/api/v1/invites/{token}/accept",
        headers={"X-CSRF-Token": csrf},
        json={"name": "双击"},
    )
    assert repeated.status_code == 409
    assert repeated.json()["error"]["code"] == "INVITE_ALREADY_USED"
    assert db_session.scalar(select(func.count(ProjectMembership.id)).where(
        ProjectMembership.project_id == project.id,
        ProjectMembership.user_id == victim.id,
    )) == 1
    assert db_session.scalar(select(func.count(AuditEvent.id)).where(
        AuditEvent.project_id == project.id,
        AuditEvent.event_type == "member_joined",
    )) == 1


def test_reauth_updates_only_current_real_session(client, db_session):
    login = secure_login(client, "owner@example.local")
    current = db_session.get(LoginSession, login["session"]["id"])
    assert current is not None
    old_authenticated_at = utc_now() - timedelta(hours=1)
    current.authenticated_at = old_authenticated_at
    sibling = LoginSession(
        user_id=current.user_id,
        token_hash="sibling-token-hash",
        csrf_hash="sibling-csrf-hash",
        user_agent="其他设备",
        ip_hash="sibling-ip-hash",
        authenticated_at=old_authenticated_at,
        last_seen_at=utc_now(),
        expires_at=utc_now() + timedelta(days=1),
    )
    db_session.add(sibling)
    db_session.commit()

    requested = client.post("/api/v1/auth/email/request-code", json={"email": "owner@example.local"})
    code = requested.json()["development_code"]
    csrf = client.cookies.get("zhuzhang_csrf")

    missing_csrf = client.post(
        "/api/v1/auth/email/reauth",
        json={"email": "owner@example.local", "code": code},
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "CSRF_INVALID"

    wrong_email = client.post(
        "/api/v1/auth/email/reauth",
        headers={"X-CSRF-Token": csrf},
        json={"email": "attacker@example.com", "code": code},
    )
    assert wrong_email.status_code == 403
    assert wrong_email.json()["error"]["code"] == "REAUTH_EMAIL_MISMATCH"

    wrong_code = "000000" if code != "000000" else "999999"
    invalid = client.post(
        "/api/v1/auth/email/reauth",
        headers={"X-CSRF-Token": csrf},
        json={"email": "owner@example.local", "code": wrong_code},
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "AUTH_CODE_INVALID"

    sessions_before = db_session.scalar(select(func.count(LoginSession.id)))
    verified = client.post(
        "/api/v1/auth/email/reauth",
        headers={"X-CSRF-Token": csrf},
        json={"email": "owner@example.local", "code": code},
    )
    assert verified.status_code == 200
    assert db_session.scalar(select(func.count(LoginSession.id))) == sessions_before
    db_session.refresh(current)
    db_session.refresh(sibling)
    assert aware(current.authenticated_at) > old_authenticated_at
    assert aware(current.authenticated_at) > utc_now() - timedelta(minutes=1)
    assert aware(sibling.authenticated_at) == old_authenticated_at


def test_login_code_success_is_compare_and_swap(client, db_session):
    requested = client.post("/api/v1/auth/email/request-code", json={"email": "cas@example.com"})
    code = requested.json()["development_code"]
    challenge = db_session.get(LoginChallenge, requested.json()["challenge_id"])
    assert challenge is not None
    db_session.expunge(challenge)

    consume_login_challenge(db_session, challenge, code)
    db_session.commit()
    stored = db_session.get(LoginChallenge, challenge.id)
    assert stored.status == "used"

    with pytest.raises(HTTPException) as repeated:
        consume_login_challenge(db_session, challenge, code)
    assert repeated.value.status_code == 409
    assert repeated.value.detail["code"] == "AUTH_CODE_ALREADY_USED"


def request_with_ip(peer: str, forwarded_for: str | None = None) -> Request:
    headers = [] if forwarded_for is None else [(b"x-forwarded-for", forwarded_for.encode())]
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers,
        "client": (peer, 12345),
        "server": ("testserver", 80),
    })


def test_request_ip_ignores_forged_forwarded_header_by_default(monkeypatch):
    monkeypatch.setattr(auth_service.settings, "trusted_proxy_cidrs", "")
    forged = request_with_ip("198.51.100.10", "203.0.113.7")
    assert request_ip(forged) == "198.51.100.10"

    monkeypatch.setattr(auth_service.settings, "trusted_proxy_cidrs", "10.0.0.0/8")
    still_untrusted = request_with_ip("198.51.100.10", "203.0.113.7")
    assert request_ip(still_untrusted) == "198.51.100.10"
    trusted = request_with_ip("10.1.2.3", "invalid, 203.0.113.7, 192.0.2.8")
    assert request_ip(trusted) == "203.0.113.7"


def test_security_headers_cover_errors_cors_and_production_hsts(client, monkeypatch):
    error = client.get("/api/v1/does-not-exist")
    assert error.status_code == 404
    expected = {
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "content-security-policy": "frame-ancestors 'none'",
        "referrer-policy": "strict-origin-when-cross-origin",
        "permissions-policy": "camera=(), microphone=(), geolocation=()",
        "x-robots-tag": "noindex, nofollow, noarchive",
    }
    for name, value in expected.items():
        assert error.headers[name] == value

    preflight = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3001",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:3001"
    assert preflight.headers["x-content-type-options"] == "nosniff"

    monkeypatch.setattr(get_settings(), "app_env", "production")
    production = client.get("/health")
    assert production.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    assert api_documentation_options() == {"docs_url": None, "redoc_url": None, "openapi_url": None}


MONEY_FIELD_CASES = [
    (ProjectCreate, {"name": "P", "city": "苏州", "area_sqm": 80, "fund_limit_cents": MAX_AMOUNT_CENTS}, "fund_limit_cents"),
    (ProjectCreate, {"name": "P", "city": "苏州", "area_sqm": 80, "fund_limit_cents": MAX_AMOUNT_CENTS, "reserve_cents": MAX_AMOUNT_CENTS}, "reserve_cents"),
    (ProjectUpdate, {"fund_limit_cents": MAX_AMOUNT_CENTS}, "fund_limit_cents"),
    (ProjectUpdate, {"reserve_cents": MAX_AMOUNT_CENTS}, "reserve_cents"),
    (BudgetCategoryUpdate, {"planned_limit_cents": MAX_AMOUNT_CENTS}, "planned_limit_cents"),
    (BaselineCreate, {"amount_cents": MAX_AMOUNT_CENTS, "reason": "确认基线"}, "amount_cents"),
    (ChangeCreate, {
        "change_type": "increase", "title": "增项", "reason": "现场调整", "content": "施工内容",
        "amount_cents": MAX_AMOUNT_CENTS, "proposed_on": date.today(), "no_attachment_acknowledged": True,
    }, "amount_cents"),
    (MilestoneCreate, {
        "name": "水电款", "planned_amount_cents": MAX_AMOUNT_CENTS, "planned_date": date.today(), "condition": "验收通过",
    }, "planned_amount_cents"),
    (PaymentCreate, {
        "amount_cents": MAX_AMOUNT_CENTS, "paid_on": date.today(), "payee": "施工方",
        "idempotency_key": "money-limit-test-0001",
    }, "amount_cents"),
    (QuoteItemUpdate, {"unit_price_cents": MAX_AMOUNT_CENTS}, "unit_price_cents"),
    (QuoteItemUpdate, {"total_cents": MAX_AMOUNT_CENTS}, "total_cents"),
]


@pytest.mark.parametrize(("model", "payload", "field"), MONEY_FIELD_CASES)
def test_all_money_request_fields_have_a_shared_upper_bound(model, payload, field):
    assert getattr(model.model_validate(payload), field) == MAX_AMOUNT_CENTS
    invalid = {**payload, field: MAX_AMOUNT_CENTS + 1}
    with pytest.raises(ValidationError):
        model.model_validate(invalid)
