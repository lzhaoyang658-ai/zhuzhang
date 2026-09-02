from __future__ import annotations

import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Notification, NotificationPreference, Project, ProjectMembership, User
from app.services.auth import aware
from app.services.notifications import reconcile_project_notifications


settings = get_settings()


def digest_is_due(preference: NotificationPreference, now: datetime) -> bool:
    if not preference.email_enabled or preference.email_digest_frequency == "off":
        return False
    interval = timedelta(days=7 if preference.email_digest_frequency == "weekly" else 1)
    return preference.last_digest_at is None or aware(preference.last_digest_at) <= now - interval


def render_digest(user: User, notifications: list[Notification], project_names: dict[str, str]) -> tuple[str, str]:
    critical = sum(1 for item in notifications if item.level == "critical")
    subject = f"筑账提醒摘要：{len(notifications)} 项待处理" + (f"，其中 {critical} 项严重" if critical else "")
    lines = [f"{user.name}，你好。", "", f"你有 {len(notifications)} 项装修预算或流程提醒：", ""]
    level_names = {"critical": "严重", "warning": "警告", "attention": "关注", "info": "提示"}
    for item in notifications:
        lines.extend([
            f"[{level_names.get(item.level, item.level)}] {item.title}",
            f"项目：{project_names.get(item.project_id, '装修项目')}",
            item.message,
            "",
        ])
    lines.extend(["请登录筑账查看对应项目事实并处理。", "本摘要不构成质量鉴定、价格审定、法律意见或付款建议。"])
    return subject, "\n".join(lines)


def deliver_digest(email: str, subject: str, content: str) -> None:
    if settings.auth_delivery_mode != "smtp" or not all((settings.smtp_host, settings.smtp_username, settings.smtp_password, settings.smtp_from_email)):
        raise RuntimeError("SMTP 邮件投递尚未配置")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from_email
    message["To"] = email
    message.set_content(content)
    if settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15) as client:
            client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as client:
            client.starttls()
            client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)


def send_due_notification_digests(db: Session, now: datetime | None = None) -> dict:
    current = now or datetime.now(timezone.utc)
    preferences = db.scalars(select(NotificationPreference).where(NotificationPreference.email_enabled.is_(True))).all()
    sent = 0
    skipped = 0
    failed = 0
    for preference in preferences:
        if not digest_is_due(preference, current):
            skipped += 1
            continue
        user = db.get(User, preference.user_id)
        if not user or user.status != "active":
            skipped += 1
            continue
        projects = db.scalars(select(Project).join(ProjectMembership).where(
            ProjectMembership.user_id == user.id,
            ProjectMembership.status == "active",
        )).all()
        for project in projects:
            reconcile_project_notifications(db, project, current)
        project_names = {project.id: project.name for project in projects}
        notifications = db.scalars(select(Notification).where(
            Notification.user_id == user.id,
            Notification.status == "active",
        ).order_by(Notification.last_triggered_at.desc())).all()
        if not notifications:
            preference.last_digest_at = current
            skipped += 1
            continue
        subject, content = render_digest(user, notifications, project_names)
        try:
            deliver_digest(user.email, subject, content)
        except (OSError, RuntimeError, smtplib.SMTPException):
            failed += 1
            continue
        preference.last_digest_at = current
        sent += 1
    db.commit()
    return {"sent": sent, "skipped": skipped, "failed": failed}
