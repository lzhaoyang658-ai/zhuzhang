from datetime import date

from sqlalchemy import func, select

from app.models import (AuditEvent, BaselineVersion, PaymentMilestone,
                        PaymentRecord, Project, ProjectMembership, Quote,
                        QuoteItem, QuoteParseJob)


def make_project(db_session, *, baseline_cents: int = 100_000_00):
    project = Project(
        name="财务一致性测试",
        city="苏州",
        area_sqm=100,
        fund_limit_cents=400_000_00,
    )
    db_session.add(project)
    db_session.flush()
    db_session.add(ProjectMembership(
        user_id="demo-owner",
        project_id=project.id,
        role="owner",
    ))
    db_session.add(BaselineVersion(
        project_id=project.id,
        version=1,
        amount_cents=baseline_cents,
    ))
    milestone = PaymentMilestone(
        project_id=project.id,
        name="工程款",
        planned_amount_cents=max(baseline_cents * 2, 10_000),
        planned_date=date.today(),
        condition="按约定付款",
        required_acceptance="无",
    )
    db_session.add(milestone)
    db_session.commit()
    return project, milestone


def make_quote(
    db_session,
    project: Project,
    *,
    quote_status: str,
    job_status: str,
    total_cents: int | None = None,
) -> Quote:
    quote = Quote(
        project_id=project.id,
        name="候选报价",
        original_name="quote.csv",
        object_key="quote.csv",
        status=quote_status,
        total_cents=total_cents or 0,
    )
    db_session.add(quote)
    db_session.flush()
    db_session.add(QuoteParseJob(
        project_id=project.id,
        quote_id=quote.id,
        status=job_status,
        progress=100 if job_status == "succeeded" else 5,
        stage="等待校对" if job_status == "succeeded" else "等待解析",
    ))
    if total_cents is not None:
        db_session.add(QuoteItem(
            project_id=project.id,
            quote_id=quote.id,
            original_name="墙面找平",
            standard_name="墙面找平",
            quantity_text="1",
            unit="项",
            unit_price_cents=total_cents,
            total_cents=total_cents,
            source_location="第 1 行",
        ))
    db_session.commit()
    return quote


def payment_payload(*, amount_cents: int, key: str, override_reason: str | None = None):
    return {
        "amount_cents": amount_cents,
        "paid_on": date.today().isoformat(),
        "payee": "施工方",
        "method": "银行转账",
        "reference": "FIN-001",
        "override_reason": override_reason,
        "idempotency_key": key,
    }


def test_quote_confirmation_requires_reviewing_successful_nonempty_positive(client, db_session):
    project, _ = make_project(db_session)

    queued = make_quote(
        db_session,
        project,
        quote_status="queued",
        job_status="queued",
    )
    queued_response = client.post(f"/api/v1/quotes/{queued.id}/confirm")
    assert queued_response.status_code == 409
    assert queued_response.json()["error"]["code"] == "QUOTE_NOT_READY"

    empty = make_quote(
        db_session,
        project,
        quote_status="reviewing",
        job_status="succeeded",
    )
    empty_response = client.post(f"/api/v1/quotes/{empty.id}/confirm")
    assert empty_response.status_code == 409
    assert empty_response.json()["error"]["code"] == "QUOTE_EMPTY"

    zero = make_quote(
        db_session,
        project,
        quote_status="reviewing",
        job_status="succeeded",
        total_cents=0,
    )
    zero_response = client.post(f"/api/v1/quotes/{zero.id}/confirm")
    assert zero_response.status_code == 409
    assert zero_response.json()["error"]["code"] == "QUOTE_TOTAL_INVALID"


def test_quote_activation_rechecks_total_and_is_idempotent_while_active(client, db_session):
    project, _ = make_project(db_session)
    quote = make_quote(
        db_session,
        project,
        quote_status="reviewing",
        job_status="succeeded",
        total_cents=23_450_00,
    )

    confirmed = client.post(f"/api/v1/quotes/{quote.id}/confirm")
    assert confirmed.status_code == 200
    first = client.post(f"/api/v1/quotes/{quote.id}/activate-baseline")
    repeated = client.post(f"/api/v1/quotes/{quote.id}/activate-baseline")

    assert first.status_code == 201
    assert repeated.status_code == 200
    assert repeated.json()["id"] == first.json()["id"]
    assert repeated.json()["already_active"] is True
    assert db_session.scalar(select(func.count(BaselineVersion.id)).where(
        BaselineVersion.project_id == project.id,
    )) == 2
    assert db_session.scalar(select(func.count(BaselineVersion.id)).where(
        BaselineVersion.project_id == project.id,
        BaselineVersion.is_active.is_(True),
    )) == 1

    baseline_events = db_session.scalar(select(func.count(AuditEvent.id)).where(
        AuditEvent.project_id == project.id,
        AuditEvent.event_type == "baseline_activated",
    ))
    assert baseline_events == 1


def test_quote_activation_rejects_stale_confirmed_total(client, db_session):
    project, _ = make_project(db_session)
    quote = make_quote(
        db_session,
        project,
        quote_status="reviewing",
        job_status="succeeded",
        total_cents=10_000,
    )
    assert client.post(f"/api/v1/quotes/{quote.id}/confirm").status_code == 200
    quote.total_cents += 1
    db_session.commit()

    response = client.post(f"/api/v1/quotes/{quote.id}/activate-baseline")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "QUOTE_TOTAL_STALE"


def test_payment_uses_post_payment_budget_and_requires_override(client, db_session):
    project, milestone = make_project(db_session, baseline_cents=1_000)
    payload = payment_payload(
        amount_cents=1_001,
        key="budget-overrun-payment-0001",
    )

    preview = client.get(
        f"/api/v1/milestones/{milestone.id}/payment-check",
        params={"proposed_amount_cents": 1_001},
    )
    assert preview.status_code == 200
    assert preview.json()["paid_after_cents"] == 1_001
    assert preview.json()["overrun_cents"] == 1

    denied = client.post(f"/api/v1/milestones/{milestone.id}/payments", json=payload)
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "PAYMENT_EXCEEDS_APPROVED_BUDGET"
    assert db_session.scalar(select(func.count(PaymentRecord.id)).where(
        PaymentRecord.project_id == project.id,
    )) == 0

    payload["override_reason"] = "已核实实际付款，先记录事实"
    recorded = client.post(f"/api/v1/milestones/{milestone.id}/payments", json=payload)
    assert recorded.status_code == 201
    assert recorded.json()["controlled"] is True


def test_payment_idempotency_compares_fingerprint_and_is_project_scoped(client, db_session):
    project, milestone = make_project(db_session, baseline_cents=20_000)
    key = "shared-payment-key-000001"
    payload = payment_payload(amount_cents=1_000, key=key)

    first = client.post(f"/api/v1/milestones/{milestone.id}/payments", json=payload)
    replay = client.post(f"/api/v1/milestones/{milestone.id}/payments", json=payload)
    changed = client.post(
        f"/api/v1/milestones/{milestone.id}/payments",
        json={**payload, "amount_cents": 1_001},
    )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["replayed"] is True
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert db_session.scalar(select(func.count(PaymentRecord.id)).where(
        PaymentRecord.project_id == project.id,
    )) == 1

    other_project, other_milestone = make_project(db_session, baseline_cents=20_000)
    other = client.post(
        f"/api/v1/milestones/{other_milestone.id}/payments",
        json=payload,
    )
    assert other.status_code == 201
    assert other.json()["id"] != first.json()["id"]
    assert db_session.scalar(select(func.count(PaymentRecord.id)).where(
        PaymentRecord.idempotency_key == key,
    )) == 2


def test_payment_idempotency_key_is_required(client, db_session):
    _, milestone = make_project(db_session, baseline_cents=20_000)
    payload = payment_payload(amount_cents=1_000, key="temporary-payment-key-0001")
    payload.pop("idempotency_key")

    response = client.post(f"/api/v1/milestones/{milestone.id}/payments", json=payload)

    assert response.status_code == 422


def test_reversal_has_explicit_unique_link_and_rejects_repeat(client, db_session):
    project, milestone = make_project(db_session, baseline_cents=20_000)
    normal_response = client.post(
        f"/api/v1/milestones/{milestone.id}/payments",
        json=payment_payload(amount_cents=1_000, key="normal-before-reversal-0001"),
    )
    assert normal_response.status_code == 201
    normal_id = normal_response.json()["id"]

    first = client.post(
        f"/api/v1/payments/{normal_id}/reverse",
        data={
            "reason": "收款账号错误",
            "idempotency_key": "reverse-payment-key-000001",
        },
    )
    replay = client.post(
        f"/api/v1/payments/{normal_id}/reverse",
        data={
            "reason": "收款账号错误",
            "idempotency_key": "reverse-payment-key-000001",
        },
    )
    changed_same_key = client.post(
        f"/api/v1/payments/{normal_id}/reverse",
        data={
            "reason": "改成另一个原因",
            "idempotency_key": "reverse-payment-key-000001",
        },
    )
    repeated_with_new_key = client.post(
        f"/api/v1/payments/{normal_id}/reverse",
        data={
            "reason": "重复操作",
            "idempotency_key": "reverse-payment-key-000002",
        },
    )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json() == {"id": first.json()["id"], "replayed": True}
    assert changed_same_key.status_code == 409
    assert changed_same_key.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert repeated_with_new_key.status_code == 409
    assert repeated_with_new_key.json()["error"]["code"] == "PAYMENT_ALREADY_REVERSED"
    reversal = db_session.scalar(select(PaymentRecord).where(
        PaymentRecord.reversal_of_payment_id == normal_id,
    ))
    assert reversal is not None
    assert reversal.id == first.json()["id"]
    assert db_session.scalar(select(func.count(PaymentRecord.id)).where(
        PaymentRecord.reversal_of_payment_id == normal_id,
    )) == 1

    reverse_a_reversal = client.post(
        f"/api/v1/payments/{reversal.id}/reverse",
        data={
            "reason": "不应允许",
            "idempotency_key": "reverse-a-reversal-key-001",
        },
    )
    assert reverse_a_reversal.status_code == 409
    assert reverse_a_reversal.json()["error"]["code"] == "PAYMENT_NOT_REVERSIBLE"


def test_reversal_requires_idempotency_key(client, db_session):
    _, milestone = make_project(db_session, baseline_cents=20_000)
    normal_response = client.post(
        f"/api/v1/milestones/{milestone.id}/payments",
        json=payment_payload(amount_cents=1_000, key="normal-for-key-check-00001"),
    )

    response = client.post(
        f"/api/v1/payments/{normal_response.json()['id']}/reverse",
        data={"reason": "操作有误"},
    )

    assert response.status_code == 422
