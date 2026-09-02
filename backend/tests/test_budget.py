from datetime import date

from app.models import BaselineVersion, ChangeOrder, PaymentMilestone, PaymentRecord, Project
from app.services.budget import calculate_budget, calculate_category_forecasts


def test_budget_uses_only_approved_and_pending_states(db_session):
    project = Project(name="测试项目", city="上海", area_sqm=90, fund_limit_cents=500_000_00)
    db_session.add(project)
    db_session.flush()
    db_session.add(BaselineVersion(project_id=project.id, version=1, amount_cents=300_000_00))
    db_session.add_all([
        ChangeOrder(project_id=project.id, change_type="increase", title="已批准", reason="测试", content="测试", amount_cents=10_000_00, status="approved"),
        ChangeOrder(project_id=project.id, change_type="decrease", title="待确认减少", reason="测试", content="测试", amount_cents=2_000_00, status="pending_confirmation"),
        ChangeOrder(project_id=project.id, change_type="increase", title="已拒绝", reason="测试", content="测试", amount_cents=99_000_00, status="rejected"),
    ])
    milestone = PaymentMilestone(project_id=project.id, name="开工", planned_amount_cents=60_000_00, planned_date=date.today(), condition="完成")
    db_session.add(milestone)
    db_session.flush()
    db_session.add(PaymentRecord(project_id=project.id, milestone_id=milestone.id, amount_cents=50_000_00, paid_on=date.today(), payee="施工方"))
    db_session.commit()

    result = calculate_budget(db_session, project)

    assert result["baseline_cents"] == 300_000_00
    assert result["approved_budget_cents"] == 310_000_00
    assert result["pending_risk_cents"] == -2_000_00
    assert result["predicted_settlement_cents"] == 308_000_00
    assert result["paid_cents"] == 50_000_00


def test_baseline_zero_rates_are_not_calculated(db_session):
    project = Project(name="无基线项目", city="杭州", area_sqm=80, fund_limit_cents=300_000_00)
    db_session.add(project)
    db_session.commit()
    result = calculate_budget(db_session, project)
    assert result["approved_overrun_rate"] is None
    assert result["predicted_overrun_rate"] is None


def test_category_forecast_nets_changes_before_clamping(db_session):
    project = Project(name="分类净额测试", city="杭州", area_sqm=80, fund_limit_cents=300_000_00)
    db_session.add(project)
    db_session.flush()
    db_session.add_all([
        ChangeOrder(
            project_id=project.id,
            change_type="decrease",
            title="先录减少项",
            reason="测试",
            content="测试",
            amount_cents=1_000_00,
            category="水电",
            status="approved",
        ),
        ChangeOrder(
            project_id=project.id,
            change_type="increase",
            title="后录增加项",
            reason="测试",
            content="测试",
            amount_cents=1_500_00,
            category="水电",
            status="pending_confirmation",
        ),
    ])
    db_session.commit()

    assert calculate_category_forecasts(db_session, project.id)["水电"] == 500_00
