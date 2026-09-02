"""enforce financial transaction integrity

Revision ID: d4a8b7c91e20
Revises: bc9f3a72d104
Create Date: 2026-09-02 10:30:00
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Sequence, Union
from uuid import UUID

import sqlalchemy as sa
from alembic import op


revision: str = "d4a8b7c91e20"
down_revision: Union[str, None] = "bc9f3a72d104"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _fail_if_rows(label: str, rows) -> None:
    values = [dict(row) for row in rows]
    if values:
        raise RuntimeError(f"{label}: {values}")


def _date_text(value: date | datetime | str) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _fingerprint(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _preflight(bind) -> tuple[list[dict], list[tuple[str, str]]]:
    _fail_if_rows(
        "存在重复的合同基线版本，迁移已中止",
        bind.execute(sa.text("""
            SELECT project_id, version, COUNT(*) AS row_count
            FROM baseline_versions
            GROUP BY project_id, version
            HAVING COUNT(*) > 1
        """)).mappings(),
    )
    _fail_if_rows(
        "同一项目存在多条生效合同基线，迁移已中止",
        bind.execute(sa.text("""
            SELECT project_id, COUNT(*) AS row_count
            FROM baseline_versions
            WHERE is_active
            GROUP BY project_id
            HAVING COUNT(*) > 1
        """)).mappings(),
    )
    _fail_if_rows(
        "存在非正数合同基线，迁移已中止",
        bind.execute(sa.text("""
            SELECT id, project_id, version, amount_cents
            FROM baseline_versions
            WHERE amount_cents <= 0
        """)).mappings(),
    )
    _fail_if_rows(
        "存在非法付款金额或流水类型，迁移已中止",
        bind.execute(sa.text("""
            SELECT id, project_id, milestone_id, amount_cents, record_type
            FROM payment_records
            WHERE amount_cents <= 0 OR record_type NOT IN ('normal', 'reversal')
        """)).mappings(),
    )
    _fail_if_rows(
        "同一项目存在重复付款幂等键，迁移已中止",
        bind.execute(sa.text("""
            SELECT project_id, idempotency_key, COUNT(*) AS row_count
            FROM payment_records
            WHERE idempotency_key IS NOT NULL
            GROUP BY project_id, idempotency_key
            HAVING COUNT(*) > 1
        """)).mappings(),
    )

    payment_rows = [dict(row) for row in bind.execute(sa.text("""
        SELECT id, project_id, milestone_id, amount_cents, paid_on, payee,
               method, reference, record_type, override_reason, idempotency_key
        FROM payment_records
    """)).mappings()]
    payments_by_id = {row["id"]: row for row in payment_rows}
    reversal_links: list[tuple[str, str]] = []
    reversal_ids_by_original: dict[str, list[str]] = {}
    invalid_reversals: list[dict] = []

    for row in payment_rows:
        if row["record_type"] != "reversal":
            continue
        reference = row["reference"] or ""
        if not reference.startswith("冲正 "):
            invalid_reversals.append({"id": row["id"], "reason": "reference 缺少冲正目标"})
            continue
        original_id = reference.removeprefix("冲正 ")
        try:
            UUID(original_id)
        except (ValueError, TypeError, AttributeError):
            invalid_reversals.append({"id": row["id"], "reason": "冲正目标不是合法 UUID"})
            continue
        original = payments_by_id.get(original_id)
        if not original:
            invalid_reversals.append({"id": row["id"], "reason": "原付款不存在"})
            continue
        if (
            original["record_type"] != "normal"
            or original["project_id"] != row["project_id"]
            or original["milestone_id"] != row["milestone_id"]
            or original["amount_cents"] != row["amount_cents"]
        ):
            invalid_reversals.append({"id": row["id"], "reason": "原付款类型、项目、节点或金额不匹配"})
            continue
        reversal_links.append((row["id"], original_id))
        reversal_ids_by_original.setdefault(original_id, []).append(row["id"])

    for original_id, reversal_ids in reversal_ids_by_original.items():
        if len(reversal_ids) > 1:
            invalid_reversals.append({
                "original_id": original_id,
                "reversal_ids": reversal_ids,
                "reason": "同一原付款存在多条冲正",
            })
    if invalid_reversals:
        raise RuntimeError(f"历史冲正关系存在歧义，迁移已中止: {invalid_reversals}")

    return payment_rows, reversal_links


def upgrade() -> None:
    bind = op.get_bind()
    payment_rows, reversal_links = _preflight(bind)

    op.add_column(
        "payment_records",
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "payment_records",
        sa.Column("reversal_of_payment_id", sa.String(length=36), nullable=True),
    )

    for row in payment_rows:
        if not row["idempotency_key"]:
            continue
        fingerprint = _fingerprint({
            "action": "create_payment",
            "project_id": row["project_id"],
            "milestone_id": row["milestone_id"],
            "amount_cents": row["amount_cents"],
            "paid_on": _date_text(row["paid_on"]),
            "payee": row["payee"],
            "method": row["method"],
            "reference": row["reference"],
            "override_reason": row["override_reason"],
        })
        bind.execute(
            sa.text("""
                UPDATE payment_records
                SET request_fingerprint = :fingerprint
                WHERE id = :payment_id
            """),
            {"fingerprint": fingerprint, "payment_id": row["id"]},
        )

    for reversal_id, original_id in reversal_links:
        bind.execute(
            sa.text("""
                UPDATE payment_records
                SET reversal_of_payment_id = :original_id
                WHERE id = :reversal_id
            """),
            {"original_id": original_id, "reversal_id": reversal_id},
        )

    with op.batch_alter_table("payment_records") as batch_op:
        batch_op.create_foreign_key(
            "fk_payment_records_reversal_of_payment_id",
            "payment_records",
            ["reversal_of_payment_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_check_constraint(
            "ck_payment_records_amount_positive",
            "amount_cents > 0",
        )
        batch_op.create_check_constraint(
            "ck_payment_records_record_type",
            "record_type IN ('normal', 'reversal')",
        )
        batch_op.create_check_constraint(
            "ck_payment_records_reversal_shape",
            "((record_type = 'normal' AND reversal_of_payment_id IS NULL) "
            "OR (record_type = 'reversal' AND reversal_of_payment_id IS NOT NULL))",
        )
        batch_op.create_check_constraint(
            "ck_payment_records_idempotency_fingerprint",
            "((idempotency_key IS NULL AND request_fingerprint IS NULL) "
            "OR (idempotency_key IS NOT NULL AND length(request_fingerprint) = 64))",
        )

    op.create_index(
        "uq_payment_records_project_idempotency_key",
        "payment_records",
        ["project_id", "idempotency_key"],
        unique=True,
    )
    op.drop_index("uq_payment_records_idempotency_key", table_name="payment_records")
    op.create_index(
        "uq_payment_records_reversal_of_payment_id",
        "payment_records",
        ["reversal_of_payment_id"],
        unique=True,
    )

    with op.batch_alter_table("baseline_versions") as batch_op:
        batch_op.create_check_constraint(
            "ck_baseline_versions_amount_positive",
            "amount_cents > 0",
        )
    op.create_index(
        "uq_baseline_versions_project_version",
        "baseline_versions",
        ["project_id", "version"],
        unique=True,
    )
    op.create_index(
        "uq_baseline_versions_active_project",
        "baseline_versions",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("is_active IS TRUE"),
        sqlite_where=sa.text("is_active = 1"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    _fail_if_rows(
        "跨项目存在重复付款幂等键，无法恢复全局唯一索引",
        bind.execute(sa.text("""
            SELECT idempotency_key, COUNT(*) AS row_count
            FROM payment_records
            WHERE idempotency_key IS NOT NULL
            GROUP BY idempotency_key
            HAVING COUNT(*) > 1
        """)).mappings(),
    )

    op.create_index(
        "uq_payment_records_idempotency_key",
        "payment_records",
        ["idempotency_key"],
        unique=True,
    )
    op.drop_index(
        "uq_payment_records_project_idempotency_key",
        table_name="payment_records",
    )
    op.drop_index(
        "uq_payment_records_reversal_of_payment_id",
        table_name="payment_records",
    )
    with op.batch_alter_table("payment_records") as batch_op:
        batch_op.drop_constraint(
            "ck_payment_records_idempotency_fingerprint",
            type_="check",
        )
        batch_op.drop_constraint("ck_payment_records_reversal_shape", type_="check")
        batch_op.drop_constraint("ck_payment_records_record_type", type_="check")
        batch_op.drop_constraint("ck_payment_records_amount_positive", type_="check")
        batch_op.drop_constraint(
            "fk_payment_records_reversal_of_payment_id",
            type_="foreignkey",
        )
        batch_op.drop_column("reversal_of_payment_id")
        batch_op.drop_column("request_fingerprint")

    op.drop_index(
        "uq_baseline_versions_active_project",
        table_name="baseline_versions",
    )
    op.drop_index(
        "uq_baseline_versions_project_version",
        table_name="baseline_versions",
    )
    with op.batch_alter_table("baseline_versions") as batch_op:
        batch_op.drop_constraint(
            "ck_baseline_versions_amount_positive",
            type_="check",
        )
