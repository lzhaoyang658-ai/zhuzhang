"""project onboarding and lifecycle

Revision ID: f29c6b4a81d0
Revises: e184a9d02c61
Create Date: 2026-08-30 18:10:00
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op


revision: str = "f29c6b4a81d0"
down_revision: Union[str, None] = "e184a9d02c61"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CATEGORIES = ["拆除与新建", "水电", "泥瓦", "木作", "油漆", "门窗", "厨卫", "主材", "软装", "家具家电", "设计与管理", "其他"]


def upgrade() -> None:
    op.add_column("projects", sa.Column("address", sa.String(length=240), nullable=True))
    op.add_column("projects", sa.Column("notes", sa.Text(), nullable=False, server_default=""))
    op.add_column("projects", sa.Column("status_before_deletion", sa.String(length=20), nullable=True))
    op.add_column("projects", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("projects", sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("projects", sa.Column("deletion_scheduled_for", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "project_budget_categories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("planned_limit_cents", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", name="uq_project_budget_category_name"),
    )
    op.create_index(op.f("ix_project_budget_categories_project_id"), "project_budget_categories", ["project_id"], unique=False)
    op.create_table(
        "project_fund_limit_history",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("previous_cents", sa.Integer(), nullable=True),
        sa.Column("new_cents", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=240), nullable=False),
        sa.Column("changed_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("changed_by_name", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["changed_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_project_fund_limit_history_changed_by_user_id"), "project_fund_limit_history", ["changed_by_user_id"], unique=False)
    op.create_index(op.f("ix_project_fund_limit_history_project_id"), "project_fund_limit_history", ["project_id"], unique=False)
    op.create_table(
        "deleted_project_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_reference_hash", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=36), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("business_record_count", sa.Integer(), nullable=False),
        sa.Column("attachment_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_deleted_project_records_owner_user_id"), "deleted_project_records", ["owner_user_id"], unique=False)
    op.create_index(op.f("ix_deleted_project_records_project_reference_hash"), "deleted_project_records", ["project_reference_hash"], unique=True)

    bind = op.get_bind()
    projects = bind.execute(sa.text("SELECT p.id, p.fund_limit_cents, COALESCE(u.id, 'demo-owner') AS owner_id, COALESCE(u.name, '项目所有者') AS owner_name FROM projects p LEFT JOIN project_memberships m ON m.project_id = p.id AND m.role = 'owner' AND m.status = 'active' LEFT JOIN users u ON u.id = m.user_id")).mappings().all()
    for project in projects:
        for order, name in enumerate(CATEGORIES, 1):
            bind.execute(sa.text("INSERT INTO project_budget_categories (id, project_id, name, planned_limit_cents, sort_order, created_at) VALUES (:id, :project_id, :name, NULL, :sort_order, CURRENT_TIMESTAMP)"), {"id": str(uuid4()), "project_id": project["id"], "name": name, "sort_order": order})
        bind.execute(sa.text("INSERT INTO project_fund_limit_history (id, project_id, previous_cents, new_cents, reason, changed_by_user_id, changed_by_name, created_at) VALUES (:id, :project_id, NULL, :amount, '迁移现有资金上限', :owner_id, :owner_name, CURRENT_TIMESTAMP)"), {"id": str(uuid4()), "project_id": project["id"], "amount": project["fund_limit_cents"], "owner_id": project["owner_id"], "owner_name": project["owner_name"]})


def downgrade() -> None:
    op.drop_index(op.f("ix_deleted_project_records_project_reference_hash"), table_name="deleted_project_records")
    op.drop_index(op.f("ix_deleted_project_records_owner_user_id"), table_name="deleted_project_records")
    op.drop_table("deleted_project_records")
    op.drop_index(op.f("ix_project_fund_limit_history_project_id"), table_name="project_fund_limit_history")
    op.drop_index(op.f("ix_project_fund_limit_history_changed_by_user_id"), table_name="project_fund_limit_history")
    op.drop_table("project_fund_limit_history")
    op.drop_index(op.f("ix_project_budget_categories_project_id"), table_name="project_budget_categories")
    op.drop_table("project_budget_categories")
    op.drop_column("projects", "deletion_scheduled_for")
    op.drop_column("projects", "deletion_requested_at")
    op.drop_column("projects", "archived_at")
    op.drop_column("projects", "status_before_deletion")
    op.drop_column("projects", "notes")
    op.drop_column("projects", "address")
