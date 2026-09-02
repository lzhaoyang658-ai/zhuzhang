from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from fastapi import HTTPException, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import LoginChallenge, LoginSession, User


settings = get_settings()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def secure_hash(value: str) -> str:
    return hmac.new(settings.auth_secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def request_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    try:
        peer_address = ipaddress.ip_address(peer)
        trusted_networks = settings.trusted_proxy_networks
    except ValueError:
        return peer
    if not any(
        peer_address.version == network.version and peer_address in network
        for network in trusted_networks
    ):
        return peer
    for candidate in request.headers.get("x-forwarded-for", "").split(","):
        value = candidate.strip()
        if not value:
            continue
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            continue
    return peer


def request_login_code(db: Session, request: Request, email: str) -> tuple[LoginChallenge, str]:
    normalized = email.strip().lower()
    now = utc_now()
    window = now - timedelta(minutes=10)
    ip_hash = secure_hash(f"ip:{request_ip(request)}")
    email_count = db.scalar(select(func.count(LoginChallenge.id)).where(LoginChallenge.email == normalized, LoginChallenge.created_at >= window)) or 0
    ip_count = db.scalar(select(func.count(LoginChallenge.id)).where(LoginChallenge.request_ip_hash == ip_hash, LoginChallenge.created_at >= window)) or 0
    if email_count >= 5 or ip_count >= 20:
        raise HTTPException(429, {"code": "AUTH_RATE_LIMITED", "message": "验证码请求过于频繁，请 10 分钟后再试"})
    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge = LoginChallenge(
        email=normalized,
        code_hash="",
        request_ip_hash=ip_hash,
        expires_at=now + timedelta(minutes=settings.auth_code_minutes),
    )
    db.add(challenge)
    db.flush()
    challenge.code_hash = secure_hash(f"code:{challenge.id}:{code}")
    db.commit()
    return challenge, code


def deliver_login_code(email: str, code: str) -> None:
    if settings.auth_delivery_mode == "development":
        return
    if settings.auth_delivery_mode != "smtp" or not all((settings.smtp_host, settings.smtp_username, settings.smtp_password, settings.smtp_from_email)):
        raise HTTPException(503, {"code": "EMAIL_DELIVERY_NOT_CONFIGURED", "message": "邮件投递尚未配置，请联系管理员"})
    message = EmailMessage()
    message["Subject"] = "筑账登录验证码"
    message["From"] = settings.smtp_from_email
    message["To"] = email
    message.set_content(f"你的筑账登录验证码是 {code}，{settings.auth_code_minutes} 分钟内有效。请勿转发给他人。")
    try:
        if settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15) as client:
                client.login(settings.smtp_username, settings.smtp_password)
                client.send_message(message)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as client:
                client.starttls()
                client.login(settings.smtp_username, settings.smtp_password)
                client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise HTTPException(503, {"code": "EMAIL_DELIVERY_FAILED", "message": "验证码暂时无法发送，请稍后重试"}) from exc


def consume_login_challenge(db: Session, challenge: LoginChallenge, code: str) -> None:
    now = utc_now()
    if aware(challenge.expires_at) < now:
        db.execute(
            update(LoginChallenge)
            .where(
                LoginChallenge.id == challenge.id,
                LoginChallenge.status == "pending",
            )
            .values(status="expired")
        )
        db.commit()
        raise HTTPException(400, {"code": "AUTH_CODE_EXPIRED", "message": "验证码已过期，请重新获取"})
    if challenge.attempts >= challenge.max_attempts:
        db.execute(
            update(LoginChallenge)
            .where(
                LoginChallenge.id == challenge.id,
                LoginChallenge.status == "pending",
            )
            .values(status="locked")
        )
        db.commit()
        raise HTTPException(429, {"code": "AUTH_CODE_LOCKED", "message": "验证码错误次数过多，请重新获取"})
    if not hmac.compare_digest(challenge.code_hash, secure_hash(f"code:{challenge.id}:{code}")):
        attempts = challenge.attempts + 1
        result = db.execute(
            update(LoginChallenge)
            .where(
                LoginChallenge.id == challenge.id,
                LoginChallenge.status == "pending",
                LoginChallenge.attempts == challenge.attempts,
            )
            .values(
                attempts=attempts,
                status="locked" if attempts >= challenge.max_attempts else "pending",
            )
        )
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(409, {
                "code": "AUTH_CODE_ALREADY_USED",
                "message": "验证码已被使用或状态已变化，请重新获取",
            })
        db.commit()
        raise HTTPException(400, {"code": "AUTH_CODE_INVALID", "message": "验证码不正确"})

    result = db.execute(
        update(LoginChallenge)
        .where(
            LoginChallenge.id == challenge.id,
            LoginChallenge.status == "pending",
            LoginChallenge.attempts == challenge.attempts,
            LoginChallenge.code_hash == challenge.code_hash,
        )
        .values(status="used", consumed_at=now)
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, {
            "code": "AUTH_CODE_ALREADY_USED",
            "message": "验证码已被使用或状态已变化，请重新获取",
        })


def verify_login_code(db: Session, email: str, code: str) -> User | None:
    normalized = email.strip().lower()
    challenge = db.scalar(select(LoginChallenge).where(LoginChallenge.email == normalized, LoginChallenge.status == "pending").order_by(LoginChallenge.created_at.desc()))
    if not challenge:
        raise HTTPException(400, {"code": "AUTH_CODE_NOT_FOUND", "message": "请先获取验证码"})
    consume_login_challenge(db, challenge, code)
    user = db.scalar(select(User).where(func.lower(User.email) == normalized))
    if user and user.status != "active":
        db.commit()
        raise HTTPException(403, {"code": "USER_DISABLED", "message": "该账号已停用"})
    return user


def issue_login_session(db: Session, request: Request, response: Response, user: User) -> LoginSession:
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    now = utc_now()
    item = LoginSession(
        user_id=user.id,
        token_hash=secure_hash(f"session:{token}"),
        csrf_hash=secure_hash(f"csrf:{csrf}"),
        user_agent=request.headers.get("user-agent", "未知设备")[:240],
        ip_hash=secure_hash(f"ip:{request_ip(request)}"),
        authenticated_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(days=settings.auth_session_days),
    )
    db.add(item)
    db.flush()
    response.set_cookie(settings.auth_cookie_name, token, max_age=settings.auth_session_days * 86400, httponly=True, secure=settings.auth_cookie_secure, samesite="lax", path="/")
    response.set_cookie(settings.auth_csrf_cookie_name, csrf, max_age=settings.auth_session_days * 86400, httponly=False, secure=settings.auth_cookie_secure, samesite="lax", path="/")
    return item


def login_session_from_request(db: Session, request: Request) -> LoginSession | None:
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        return None
    item = db.scalar(select(LoginSession).where(LoginSession.token_hash == secure_hash(f"session:{token}"), LoginSession.revoked_at.is_(None)))
    if not item or aware(item.expires_at) <= utc_now():
        return None
    return item


def validate_csrf(request: Request, item: LoginSession) -> None:
    provided = request.headers.get("X-CSRF-Token", "")
    if not provided or not hmac.compare_digest(item.csrf_hash, secure_hash(f"csrf:{provided}")):
        raise HTTPException(403, {"code": "CSRF_INVALID", "message": "安全校验已失效，请刷新页面后重试"})


def require_recent_login(request: Request) -> None:
    item = getattr(request.state, "login_session", None)
    if item is None and settings.demo_identity_enabled and getattr(request.state, "demo_identity", False):
        return
    if item is None or aware(item.authenticated_at) < utc_now() - timedelta(minutes=settings.auth_recent_minutes):
        raise HTTPException(401, {"code": "RECENT_LOGIN_REQUIRED", "message": "这项操作需要重新验证邮箱"})


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(settings.auth_cookie_name, path="/")
    response.delete_cookie(settings.auth_csrf_cookie_name, path="/")
