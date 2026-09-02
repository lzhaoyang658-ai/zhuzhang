"""family collaborators and project permissions

Revision ID: c7e4f1209a31
Revises: b89e31c6d2a4
Create Date: 2026-08-30 14:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c7e4f1209a31"
down_revision: Union[str, None] = "b89e31c6d2a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("email", sa.String(length=240), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_table(
        "project_memberships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "project_id", name="uq_project_membership_user_project"),
    )
    op.create_index(op.f("ix_project_memberships_project_id"), "project_memberships", ["project_id"], unique=False)
    op.create_index(op.f("ix_project_memberships_user_id"), "project_memberships", ["user_id"], unique=False)
    op.create_table(
        "project_invites",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=240), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("invited_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_project_invites_email"), "project_invites", ["email"], unique=False)
    op.create_index(op.f("ix_project_invites_invited_by_user_id"), "project_invites", ["invited_by_user_id"], unique=False)
    op.create_index(op.f("ix_project_invites_project_id"), "project_invites", ["project_id"], unique=False)
    op.create_index(op.f("ix_project_invites_status"), "project_invites", ["status"], unique=False)
    op.create_index(op.f("ix_project_invites_token_hash"), "project_invites", ["token_hash"], unique=True)

    op.execute(sa.text("INSERT INTO users (id, name, email, status, created_at) VALUES ('demo-owner', '林然', 'owner@example.local', 'active', CURRENT_TIMESTAMP)"))
    op.execute(sa.text("INSERT INTO project_memberships (id, user_id, project_id, role, status, created_at) SELECT id, 'demo-owner', id, 'owner', 'active', CURRENT_TIMESTAMP FROM projects"))


def downgrade() -> None:
    op.drop_index(op.f("ix_project_invites_token_hash"), table_name="project_invites")
    op.drop_index(op.f("ix_project_invites_status"), table_name="project_invites")
    op.drop_index(op.f("ix_project_invites_project_id"), table_name="project_invites")
    op.drop_index(op.f("ix_project_invites_invited_by_user_id"), table_name="project_invites")
    op.drop_index(op.f("ix_project_invites_email"), table_name="project_invites")
    op.drop_table("project_invites")
    op.drop_index(op.f("ix_project_memberships_user_id"), table_name="project_memberships")
    op.drop_index(op.f("ix_project_memberships_project_id"), table_name="project_memberships")
    op.drop_table("project_memberships")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
