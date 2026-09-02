from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import BaselineVersion, ChangeOrder, PaymentMilestone, PaymentRecord, Project, QuoteItem


APPROVED_STATUSES = {"approved", "implemented", "accepted", "settled"}
PENDING_STATUSES = {"pending_confirmation", "revising"}


def signed_change(change: ChangeOrder) -> int:
    return change.amount_cents if change.change_type == "increase" else -change.amount_cents


def calculate_category_forecasts(db: Session, project_id: str) -> dict[str, int]:
    baseline = db.scalar(
        select(BaselineVersion)
        .where(BaselineVersion.project_id == project_id, BaselineVersion.is_active.is_(True))
        .order_by(BaselineVersion.version.desc())
    )
    base_amounts: dict[str, int] = {}
    if baseline and baseline.source_quote_id:
        rows = db.execute(
            select(QuoteItem.category, func.sum(QuoteItem.total_cents))
            .where(QuoteItem.quote_id == baseline.source_quote_id)
            .group_by(QuoteItem.category)
        ).all()
        base_amounts.update({name: amount or 0 for name, amount in rows})

    deltas: dict[str, int] = {}
    changes = db.scalars(select(ChangeOrder).where(ChangeOrder.project_id == project_id)).all()
    for change in changes:
        if change.status in APPROVED_STATUSES | PENDING_STATUSES:
            deltas[change.category] = deltas.get(change.category, 0) + signed_change(change)

    return {
        name: max(0, base_amounts.get(name, 0) + deltas.get(name, 0))
        for name in base_amounts.keys() | deltas.keys()
    }


def calculate_budget(db: Session, project: Project) -> dict:
    baseline = db.scalar(
        select(BaselineVersion)
        .where(BaselineVersion.project_id == project.id, BaselineVersion.is_active.is_(True))
        .order_by(BaselineVersion.version.desc())
    )
    baseline_cents = baseline.amount_cents if baseline else 0
    changes = db.scalars(select(ChangeOrder).where(ChangeOrder.project_id == project.id)).all()
    approved_delta = sum(signed_change(item) for item in changes if item.status in APPROVED_STATUSES)
    pending_delta = sum(signed_change(item) for item in changes if item.status in PENDING_STATUSES)
    approved = max(0, baseline_cents + approved_delta)
    predicted = max(0, approved + pending_delta)
    payment_rows = db.scalars(select(PaymentRecord).where(PaymentRecord.project_id == project.id)).all()
    paid = sum(-row.amount_cents if row.record_type == "reversal" else row.amount_cents for row in payment_rows)
    next_30 = db.scalar(
        select(func.coalesce(func.sum(PaymentMilestone.planned_amount_cents), 0)).where(
            PaymentMilestone.project_id == project.id,
            PaymentMilestone.planned_date >= date.today(),
            PaymentMilestone.planned_date <= date.today() + timedelta(days=30),
        )
    ) or 0
    return {
        "fund_limit_cents": project.fund_limit_cents,
        "baseline_cents": baseline_cents,
        "baseline_version": baseline.version if baseline else None,
        "approved_change_cents": approved_delta,
        "approved_budget_cents": approved,
        "pending_risk_cents": pending_delta,
        "predicted_settlement_cents": predicted,
        "paid_cents": paid,
        "remaining_funds_cents": project.fund_limit_cents - predicted,
        "next_30_days_cents": next_30,
        "approved_overrun_rate": None if baseline_cents == 0 else approved_delta / baseline_cents,
        "predicted_overrun_rate": None if baseline_cents == 0 else (predicted - baseline_cents) / baseline_cents,
        "payment_progress": None if approved == 0 else paid / approved,
    }


def build_alerts(budget: dict, pending_count: int, next_milestone_incomplete: bool) -> list[dict]:
    alerts: list[dict] = []
    if pending_count:
        alerts.append({"code": "A1", "level": "info", "title": f"{pending_count} 项增减项等待确认", "action": "查看并处理待确认增项"})
    if budget["approved_budget_cents"] > budget["baseline_cents"]:
        alerts.append({"code": "A2", "level": "attention", "title": "已批准预算高于合同基线", "action": "查看主要增项来源"})
    limit = budget["fund_limit_cents"]
    predicted = budget["predicted_settlement_cents"]
    if limit and predicted > limit:
        alerts.append({"code": "A4", "level": "critical", "title": "预测结算已超过资金上限", "action": "检查未确定项目和风险预留"})
    elif limit and predicted >= limit * 0.9:
        alerts.append({"code": "A3", "level": "warning", "title": "预测结算已达到资金上限 90%", "action": "检查预算风险"})
    if next_milestone_incomplete:
        alerts.append({"code": "A6", "level": "warning", "title": "下一付款节点尚缺验收记录", "action": "付款前补充验收"})
    return alerts
