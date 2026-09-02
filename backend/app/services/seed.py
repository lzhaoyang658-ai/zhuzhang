from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (AcceptanceRecord, BaselineVersion, ChangeOrder, LoginSession,
                        PaymentMilestone, PaymentRecord, Project,
                        ProjectBudgetCategory, ProjectFundLimitHistory,
                        ProjectMembership, User)
from app.services.audit import record_event


DEFAULT_CATEGORIES = ["拆除与新建", "水电", "泥瓦", "木作", "油漆", "门窗", "厨卫", "主材", "软装", "家具家电", "设计与管理", "其他"]
DEMO_USER_ID = "demo-owner"


def ensure_project_defaults(db: Session, project: Project, owner: User) -> None:
    existing_categories = set(db.scalars(select(ProjectBudgetCategory.name).where(ProjectBudgetCategory.project_id == project.id)).all())
    db.add_all([ProjectBudgetCategory(project_id=project.id, name=name, sort_order=order) for order, name in enumerate(DEFAULT_CATEGORIES, 1) if name not in existing_categories])
    if not db.scalar(select(ProjectFundLimitHistory.id).where(ProjectFundLimitHistory.project_id == project.id)):
        db.add(ProjectFundLimitHistory(project_id=project.id, previous_cents=None, new_cents=project.fund_limit_cents, reason="创建项目", changed_by_user_id=owner.id, changed_by_name=owner.name))


def seed_demo(db: Session) -> bool:
    # Demo data is an explicit local-development convenience. Never attach the
    # demo identity to an existing project: doing so would silently grant it
    # owner access to real user data.
    if db.scalar(select(Project.id).limit(1)):
        return False

    owner = db.get(User, DEMO_USER_ID)
    if not owner:
        owner = User(id=DEMO_USER_ID, name="林然", email="owner@example.local")
        db.add(owner)
        db.flush()
    elif owner.status != "active":
        owner.status = "active"
    today = date.today()
    project = Project(
        name="云栖家 · 全屋改造",
        city="上海",
        area_sqm=118,
        area_basis="套内面积",
        renovation_type="半包",
        planned_start=today - timedelta(days=38),
        planned_end=today + timedelta(days=74),
        fund_limit_cents=450_000_00,
        reserve_cents=35_000_00,
        status="施工中",
    )
    db.add(project)
    db.flush()
    db.add(ProjectMembership(user_id=owner.id, project_id=project.id, role="owner"))
    ensure_project_defaults(db, project, owner)
    baseline = BaselineVersion(project_id=project.id, version=2, amount_cents=326_800_00, reason="补充协议纳入水电调整")
    db.add(baseline)
    changes = [
        ChangeOrder(project_id=project.id, change_type="increase", title="厨房墙面找平追加", reason="拆除后墙面平整度不满足铺贴要求", content="厨房东侧墙面新增找平 18㎡，含人工与辅料。", amount_cents=6_800_00, status="approved", area="厨房", category="泥瓦", proposer="施工方", proposed_on=today - timedelta(days=8)),
        ChangeOrder(project_id=project.id, change_type="increase", title="主卧双控线路调整", reason="床位方案变更", content="主卧床头增加双控点位 2 组，包含开槽与线管。", amount_cents=2_200_00, status="pending_confirmation", area="主卧", category="水电", proposer="业主", proposed_on=today - timedelta(days=2)),
        ChangeOrder(project_id=project.id, change_type="decrease", title="客卫壁龛取消", reason="墙体厚度不足", content="取消客卫淋浴区壁龛施工。", amount_cents=1_200_00, status="approved", area="客卫", category="泥瓦", proposer="施工方", proposed_on=today - timedelta(days=12)),
    ]
    db.add_all(changes)
    milestones = [
        PaymentMilestone(project_id=project.id, name="开工款", planned_amount_cents=65_360_00, planned_date=today - timedelta(days=35), condition="进场交底完成", required_acceptance="开工交底记录", sort_order=1),
        PaymentMilestone(project_id=project.id, name="水电阶段款", planned_amount_cents=98_040_00, planned_date=today + timedelta(days=4), condition="水电隐蔽工程验收完成", required_acceptance="水电验收与现场照片", sort_order=2),
        PaymentMilestone(project_id=project.id, name="泥木阶段款", planned_amount_cents=81_700_00, planned_date=today + timedelta(days=31), condition="泥木阶段验收完成", required_acceptance="泥木验收记录", sort_order=3),
        PaymentMilestone(project_id=project.id, name="竣工款", planned_amount_cents=65_360_00, planned_date=today + timedelta(days=70), condition="竣工验收完成", required_acceptance="竣工问题清单", sort_order=4),
        PaymentMilestone(project_id=project.id, name="尾款", planned_amount_cents=16_340_00, planned_date=today + timedelta(days=100), condition="质保资料与问题关闭", required_acceptance="尾款确认记录", sort_order=5),
    ]
    db.add_all(milestones)
    db.flush()
    db.add(AcceptanceRecord(project_id=project.id, milestone_id=milestones[0].id, accepted_on=today - timedelta(days=36), result="passed", notes="开工交底完成", open_issues=0))
    db.add(PaymentRecord(project_id=project.id, milestone_id=milestones[0].id, amount_cents=65_360_00, paid_on=today - timedelta(days=35), payee="筑研空间设计工程", method="银行转账", reference="首期工程款", controlled=True))
    record_event(db, project_id=project.id, event_type="project_created", object_type="project", object_id=project.id, title="创建装修项目", detail="项目进入施工中", actor="演示数据")
    record_event(db, project_id=project.id, event_type="baseline_activated", object_type="baseline", object_id=baseline.id, title="合同基线 V2 已生效", detail="补充协议纳入水电调整", amount_delta_cents=baseline.amount_cents, actor="演示数据")
    for item in changes:
        record_event(db, project_id=project.id, event_type="change_created", object_type="change", object_id=item.id, title=item.title, detail="已批准" if item.status == "approved" else "等待确认", amount_delta_cents=item.amount_cents if item.change_type == "increase" else -item.amount_cents, actor="演示数据")
    record_event(db, project_id=project.id, event_type="payment_recorded", object_type="payment", object_id=milestones[0].id, title="已记录开工款", detail="银行转账", amount_delta_cents=65_360_00, actor="演示数据")
    db.commit()
    return True


def demo_access_snapshot(db: Session) -> dict[str, object]:
    owner = db.get(User, DEMO_USER_ID)
    active_project_ids = list(db.scalars(select(ProjectMembership.project_id).where(
        ProjectMembership.user_id == DEMO_USER_ID,
        ProjectMembership.status == "active",
    )).all())
    active_session_ids = list(db.scalars(select(LoginSession.id).where(
        LoginSession.user_id == DEMO_USER_ID,
        LoginSession.revoked_at.is_(None),
    )).all())
    return {
        "user_active": bool(owner and owner.status == "active"),
        "active_project_ids": active_project_ids,
        "active_session_ids": active_session_ids,
    }


def assert_production_demo_isolation(db: Session) -> None:
    snapshot = demo_access_snapshot(db)
    if snapshot["user_active"] or snapshot["active_project_ids"] or snapshot["active_session_ids"]:
        raise RuntimeError("生产数据库仍存在可用的 demo-owner 身份、项目权限或会话，请先完成演示账号隔离")
