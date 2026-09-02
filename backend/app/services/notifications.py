from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import (AcceptanceRecord, ChangeOrder, Notification,
                        NotificationPreference, PaymentMilestone, PaymentRecord, Project,
                        ProjectBudgetCategory, ProjectMembership)
from app.services.auth import aware
from app.services.budget import (PENDING_STATUSES, calculate_budget,
                                 calculate_category_forecasts)


LEVEL_ORDER = {"critical": 0, "warning": 1, "attention": 2, "info": 3}


def ensure_notification_preference(db: Session, user_id: str) -> NotificationPreference:
    item = db.scalar(select(NotificationPreference).where(NotificationPreference.user_id == user_id))
    if item:
        return item
    now = datetime.now(timezone.utc)
    if db.get_bind().dialect.name == "sqlite":
        db.execute(sqlite_insert(NotificationPreference).values(
            id=str(uuid4()), user_id=user_id, email_enabled=False,
            email_digest_frequency="off", updated_at=now,
        ).on_conflict_do_nothing(index_elements=["user_id"]))
        db.flush()
        return db.scalar(select(NotificationPreference).where(NotificationPreference.user_id == user_id))
    item = NotificationPreference(user_id=user_id, email_enabled=False, email_digest_frequency="off")
    db.add(item)
    db.flush()
    return item


def _get_or_create_notification(db: Session, key: str, project: Project, member: ProjectMembership,
                                alert: dict, current: datetime) -> Notification:
    item = db.scalar(select(Notification).where(Notification.dedupe_key == key))
    if item:
        return item
    if db.get_bind().dialect.name == "sqlite":
        values = {
            "id": str(uuid4()), "user_id": member.user_id, "project_id": project.id,
            "kind": "risk", "dedupe_key": key, "status": "active",
            "read_at": None, "resolved_at": None, "first_triggered_at": current,
            "last_triggered_at": current, "occurrence_count": 1,
            "created_at": current, "updated_at": current, **alert,
        }
        db.execute(sqlite_insert(Notification).values(**values).on_conflict_do_nothing(index_elements=["dedupe_key"]))
        db.flush()
        return db.scalar(select(Notification).where(Notification.dedupe_key == key))
    item = Notification(
        user_id=member.user_id, project_id=project.id, kind="risk",
        dedupe_key=key, first_triggered_at=current, last_triggered_at=current, **alert,
    )
    db.add(item)
    db.flush()
    return item


def create_event_notifications(
    db: Session,
    project: Project,
    *,
    code: str,
    level: str,
    title: str,
    message: str,
    object_type: str,
    object_id: str | None,
    action_path: str,
    event_state: str = "completed",
    exclude_user_ids: set[str] | None = None,
) -> list[Notification]:
    """Create one durable, idempotent event notification per active project member."""
    current = datetime.now(timezone.utc)
    excluded = exclude_user_ids or set()
    members = db.scalars(select(ProjectMembership).where(
        ProjectMembership.project_id == project.id,
        ProjectMembership.status == "active",
    )).all()
    alert = _definition(code, level, title, message, object_type, object_id, action_path)
    output: list[Notification] = []
    for member in members:
        if member.user_id in excluded:
            continue
        target = object_id or project.id
        key = f"{member.user_id}:{project.id}:event:{code}:{object_type}:{target}:{event_state}"
        item = db.scalar(select(Notification).where(Notification.dedupe_key == key))
        if not item:
            values = {
                "id": str(uuid4()), "user_id": member.user_id, "project_id": project.id,
                "kind": "event", "dedupe_key": key, "status": "active",
                "read_at": None, "resolved_at": None, "first_triggered_at": current,
                "last_triggered_at": current, "occurrence_count": 1,
                "created_at": current, "updated_at": current, **alert,
            }
            if db.get_bind().dialect.name == "sqlite":
                db.execute(sqlite_insert(Notification).values(**values).on_conflict_do_nothing(index_elements=["dedupe_key"]))
                db.flush()
                item = db.scalar(select(Notification).where(Notification.dedupe_key == key))
            else:
                item = Notification(**values)
                db.add(item)
                db.flush()
        if item:
            output.append(item)
    return output


def _definition(code: str, level: str, title: str, message: str, object_type: str,
                object_id: str | None, action_path: str) -> dict:
    return {
        "code": code,
        "level": level,
        "title": title,
        "message": message,
        "object_type": object_type,
        "object_id": object_id,
        "action_path": action_path,
    }


def evaluate_project_risks(db: Session, project: Project, now: datetime | None = None) -> list[dict]:
    current = now or datetime.now(timezone.utc)
    if project.status in {"已归档", "待删除"}:
        return []
    budget = calculate_budget(db, project)
    changes = db.scalars(select(ChangeOrder).where(ChangeOrder.project_id == project.id)).all()
    milestones = db.scalars(select(PaymentMilestone).where(PaymentMilestone.project_id == project.id)).all()
    action_root = f"/?project={project.id}"
    alerts: list[dict] = []

    for change in changes:
        if change.status in PENDING_STATUSES:
            alerts.append(_definition(
                "A1", "info", f"增减项等待确认：{change.title}",
                f"{change.category} · {'增加' if change.change_type == 'increase' else '减少'} ¥{change.amount_cents / 100:,.2f}",
                "change", change.id, f"{action_root}&tab=changes",
            ))
        if change.status == "pending_confirmation" and aware(change.updated_at) <= current - timedelta(hours=48):
            alerts.append(_definition(
                "A7", "info", f"确认已等待超过 48 小时：{change.title}",
                "可以提醒确认人，或撤回后补充范围、金额与附件。",
                "change", change.id, f"{action_root}&tab=changes",
            ))

    if budget["baseline_cents"] and budget["approved_budget_cents"] > budget["baseline_cents"]:
        difference = budget["approved_budget_cents"] - budget["baseline_cents"]
        alerts.append(_definition(
            "A2", "attention", "已批准预算高于合同基线",
            f"已批准增项净增加 ¥{difference / 100:,.2f}，建议核对主要来源。",
            "project", project.id, f"{action_root}&tab=budget",
        ))

    predicted = budget["predicted_settlement_cents"]
    limit = budget["fund_limit_cents"]
    if limit and predicted > limit:
        alerts.append(_definition(
            "A4", "critical", "预测结算已超过资金上限",
            f"当前预测超出 ¥{(predicted - limit) / 100:,.2f}，付款或新增项前应先复核。",
            "project", project.id, f"{action_root}&tab=budget",
        ))
    elif limit and predicted >= limit * 0.9:
        alerts.append(_definition(
            "A3", "warning", "预测结算已达到资金上限 90%",
            f"当前预计占用资金上限 {predicted / limit:.0%}，请检查待确认项目和风险预留。",
            "project", project.id, f"{action_root}&tab=budget",
        ))

    category_amounts = calculate_category_forecasts(db, project.id)
    categories = db.scalars(select(ProjectBudgetCategory).where(
        ProjectBudgetCategory.project_id == project.id,
        ProjectBudgetCategory.planned_limit_cents.is_not(None),
    )).all()
    for category in categories:
        forecast = category_amounts.get(category.name, 0)
        if category.planned_limit_cents is not None and forecast > category.planned_limit_cents:
            alerts.append(_definition(
                "A5", "warning", f"{category.name}分类预算预计超限",
                f"已知预测金额超出分类上限 ¥{(forecast - category.planned_limit_cents) / 100:,.2f}。",
                "budget_category", category.id, f"{action_root}&tab=budget",
            ))

    due_cutoff = current.date() + timedelta(days=7)
    for milestone in milestones:
        payment_rows = db.scalars(select(PaymentRecord).where(PaymentRecord.milestone_id == milestone.id)).all()
        paid = sum(-row.amount_cents if row.record_type == "reversal" else row.amount_cents for row in payment_rows)
        if paid < milestone.planned_amount_cents and milestone.planned_date <= due_cutoff:
            latest = db.scalar(select(AcceptanceRecord).where(
                AcceptanceRecord.milestone_id == milestone.id,
            ).order_by(AcceptanceRecord.created_at.desc()))
            incomplete = not latest or latest.result == "failed" or latest.open_issues > 0
            if incomplete:
                detail = "尚无验收记录" if not latest else ("验收未通过" if latest.result == "failed" else f"仍有 {latest.open_issues} 项问题")
                alerts.append(_definition(
                    "A6", "warning", f"付款节点临近但条件不完整：{milestone.name}",
                    f"计划日期 {milestone.planned_date.isoformat()}，{detail}。",
                    "milestone", milestone.id, f"{action_root}&tab=payments",
                ))
        if paid > milestone.planned_amount_cents:
            alerts.append(_definition(
                "A8", "critical", f"实付已超过节点计划：{milestone.name}",
                f"节点实付超出计划 ¥{(paid - milestone.planned_amount_cents) / 100:,.2f}，请核对付款与增项依据。",
                "milestone", milestone.id, f"{action_root}&tab=payments",
            ))

    return sorted(alerts, key=lambda item: (LEVEL_ORDER[item["level"]], item["code"], item["title"]))


def reconcile_project_notifications(db: Session, project: Project, now: datetime | None = None) -> list[Notification]:
    current = now or datetime.now(timezone.utc)
    definitions = evaluate_project_risks(db, project, current)
    members = db.scalars(select(ProjectMembership).where(
        ProjectMembership.project_id == project.id,
        ProjectMembership.status == "active",
    )).all()
    active_keys: set[str] = set()
    output: list[Notification] = []
    for member in members:
        for alert in definitions:
            target = alert["object_id"] or project.id
            key = f"{member.user_id}:{project.id}:{alert['code']}:{alert['object_type']}:{target}"
            active_keys.add(key)
            item = _get_or_create_notification(db, key, project, member, alert, current)
            if item:
                item.title = alert["title"]
                item.message = alert["message"]
                item.level = alert["level"]
                item.action_path = alert["action_path"]
                if item.status == "resolved":
                    item.status = "active"
                    item.resolved_at = None
                    item.read_at = None
                    item.last_triggered_at = current
                    item.occurrence_count += 1
            output.append(item)

    active_rows = db.scalars(select(Notification).where(
        Notification.project_id == project.id,
        Notification.kind == "risk",
        Notification.status == "active",
    )).all()
    for item in active_rows:
        if item.dedupe_key not in active_keys:
            item.status = "resolved"
            item.resolved_at = current
    db.flush()
    return output


def notification_payload(item: Notification, project_name: str) -> dict:
    return {
        "id": item.id,
        "project_id": item.project_id,
        "project_name": project_name,
        "kind": item.kind,
        "code": item.code,
        "level": item.level,
        "title": item.title,
        "message": item.message,
        "object_type": item.object_type,
        "object_id": item.object_id,
        "action_path": item.action_path,
        "status": item.status,
        "read_at": item.read_at,
        "resolved_at": item.resolved_at,
        "first_triggered_at": item.first_triggered_at,
        "last_triggered_at": item.last_triggered_at,
        "occurrence_count": item.occurrence_count,
    }
