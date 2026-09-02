"""secure email login sessions

Revision ID: e184a9d02c61
Revises: c7e4f1209a31
Create Date: 2026-08-30 16:30:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e184a9d02c61"
down_revision: Union[str, None] = "c7e4f1209a31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text("UPDATE users SET email_verified_at = CURRENT_TIMESTAMP WHERE email_verified_at IS NULL"))
    op.create_table(
        "login_challenges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=240), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("request_ip_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_login_challenges_email"), "login_challenges", ["email"], unique=False)
    op.create_index(op.f("ix_login_challenges_request_ip_hash"), "login_challenges", ["request_ip_hash"], unique=False)
    op.create_index(op.f("ix_login_challenges_status"), "login_challenges", ["status"], unique=False)
    op.create_table(
        "login_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_hash", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=240), nullable=False),
        sa.Column("ip_hash", sa.String(length=64), nullable=False),
        sa.Column("authenticated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_login_sessions_token_hash"), "login_sessions", ["token_hash"], unique=True)
    op.create_index(op.f("ix_login_sessions_user_id"), "login_sessions", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_login_sessions_user_id"), table_name="login_sessions")
    op.drop_index(op.f("ix_login_sessions_token_hash"), table_name="login_sessions")
    op.drop_table("login_sessions")
    op.drop_index(op.f("ix_login_challenges_status"), table_name="login_challenges")
    op.drop_index(op.f("ix_login_challenges_request_ip_hash"), table_name="login_challenges")
    op.drop_index(op.f("ix_login_challenges_email"), table_name="login_challenges")
    op.drop_table("login_challenges")
    op.drop_column("users", "email_verified_at")
