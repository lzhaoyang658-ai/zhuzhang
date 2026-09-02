"""export worker leases and archive parts

Revision ID: d61a8c4e7f20
Revises: c72f61a04d8e
Create Date: 2026-08-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d61a8c4e7f20"
down_revision: str | Sequence[str] | None = "c72f61a04d8e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("project_export_jobs", sa.Column("part_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("project_export_jobs", sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("project_export_jobs", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"))
    op.add_column("project_export_jobs", sa.Column("lease_owner", sa.String(length=120), nullable=True))
    op.add_column("project_export_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("project_export_jobs", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_project_export_jobs_lease_owner", "project_export_jobs", ["lease_owner"])
    op.create_index("ix_project_export_jobs_lease_expires_at", "project_export_jobs", ["lease_expires_at"])
    op.create_index("ix_project_export_jobs_next_attempt_at", "project_export_jobs", ["next_attempt_at"])
    op.create_table(
        "project_export_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("part_number", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=180), nullable=False),
        sa.Column("object_key", sa.String(length=240), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_backend", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["project_export_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index("ix_project_export_artifacts_job_id", "project_export_artifacts", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_project_export_artifacts_job_id", table_name="project_export_artifacts")
    op.drop_table("project_export_artifacts")
    op.drop_index("ix_project_export_jobs_next_attempt_at", table_name="project_export_jobs")
    op.drop_index("ix_project_export_jobs_lease_expires_at", table_name="project_export_jobs")
    op.drop_index("ix_project_export_jobs_lease_owner", table_name="project_export_jobs")
    op.drop_column("project_export_jobs", "next_attempt_at")
    op.drop_column("project_export_jobs", "lease_expires_at")
    op.drop_column("project_export_jobs", "lease_owner")
    op.drop_column("project_export_jobs", "max_attempts")
    op.drop_column("project_export_jobs", "attempt_count")
    op.drop_column("project_export_jobs", "part_count")
