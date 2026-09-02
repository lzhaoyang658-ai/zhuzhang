"""quote worker leases and worker heartbeats

Revision ID: e83b4d9a6f12
Revises: d61a8c4e7f20
Create Date: 2026-08-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e83b4d9a6f12"
down_revision: str | Sequence[str] | None = "d61a8c4e7f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("quote_parse_jobs", sa.Column("lease_owner", sa.String(length=120), nullable=True))
    op.add_column("quote_parse_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("quote_parse_jobs", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_quote_parse_jobs_lease_owner", "quote_parse_jobs", ["lease_owner"])
    op.create_index("ix_quote_parse_jobs_lease_expires_at", "quote_parse_jobs", ["lease_expires_at"])
    op.create_index("ix_quote_parse_jobs_next_attempt_at", "quote_parse_jobs", ["next_attempt_at"])
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(length=160), nullable=False),
        sa.Column("queue_name", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_job_id", sa.String(length=36), nullable=True),
        sa.Column("processed_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("worker_id"),
    )
    op.create_index("ix_worker_heartbeats_queue_name", "worker_heartbeats", ["queue_name"])
    op.create_index("ix_worker_heartbeats_last_seen_at", "worker_heartbeats", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_worker_heartbeats_last_seen_at", table_name="worker_heartbeats")
    op.drop_index("ix_worker_heartbeats_queue_name", table_name="worker_heartbeats")
    op.drop_table("worker_heartbeats")
    op.drop_index("ix_quote_parse_jobs_next_attempt_at", table_name="quote_parse_jobs")
    op.drop_index("ix_quote_parse_jobs_lease_expires_at", table_name="quote_parse_jobs")
    op.drop_index("ix_quote_parse_jobs_lease_owner", table_name="quote_parse_jobs")
    op.drop_column("quote_parse_jobs", "next_attempt_at")
    op.drop_column("quote_parse_jobs", "lease_expires_at")
    op.drop_column("quote_parse_jobs", "lease_owner")
