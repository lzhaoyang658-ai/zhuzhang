"""notifications and risk alerts

Revision ID: 9ca27d6f8b41
Revises: f29c6b4a81d0
Create Date: 2026-08-30 21:30:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "9ca27d6f8b41"
down_revision: Union[str, None] = "f29c6b4a81d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("change_orders", sa.Column("category", sa.String(length=80), nullable=False, server_default="其他"))
    with op.batch_alter_table("baseline_versions") as batch_op:
        batch_op.add_column(sa.Column("source_quote_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key("fk_baseline_versions_source_quote_id", "quotes", ["source_quote_id"], ["id"])
    op.create_index(op.f("ix_baseline_versions_source_quote_id"), "baseline_versions", ["source_quote_id"], unique=False)

    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("email_enabled", sa.Boolean(), nullable=False),
        sa.Column("email_digest_frequency", sa.String(length=20), nullable=False),
        sa.Column("last_digest_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_notification_preference_user"),
    )
    op.create_index(op.f("ix_notification_preferences_user_id"), "notification_preferences", ["user_id"], unique=False)
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("object_type", sa.String(length=40), nullable=False),
        sa.Column("object_id", sa.String(length=36), nullable=True),
        sa.Column("action_path", sa.String(length=240), nullable=False),
        sa.Column("dedupe_key", sa.String(length=240), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_notification_dedupe_key"),
    )
    op.create_index(op.f("ix_notifications_code"), "notifications", ["code"], unique=False)
    op.create_index(op.f("ix_notifications_dedupe_key"), "notifications", ["dedupe_key"], unique=True)
    op.create_index(op.f("ix_notifications_level"), "notifications", ["level"], unique=False)
    op.create_index(op.f("ix_notifications_project_id"), "notifications", ["project_id"], unique=False)
    op.create_index(op.f("ix_notifications_status"), "notifications", ["status"], unique=False)
    op.create_index(op.f("ix_notifications_user_id"), "notifications", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_notifications_user_id"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_status"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_project_id"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_level"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_dedupe_key"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_code"), table_name="notifications")
    op.drop_table("notifications")
    op.drop_index(op.f("ix_notification_preferences_user_id"), table_name="notification_preferences")
    op.drop_table("notification_preferences")
    op.drop_index(op.f("ix_baseline_versions_source_quote_id"), table_name="baseline_versions")
    with op.batch_alter_table("baseline_versions") as batch_op:
        batch_op.drop_constraint("fk_baseline_versions_source_quote_id", type_="foreignkey")
        batch_op.drop_column("source_quote_id")
    op.drop_column("change_orders", "category")
