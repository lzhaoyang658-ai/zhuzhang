"""async project exports

Revision ID: b4e87f19c632
Revises: 9ca27d6f8b41
Create Date: 2026-08-31 09:30:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b4e87f19c632"
down_revision: Union[str, None] = "9ca27d6f8b41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_export_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("requested_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=120), nullable=False),
        sa.Column("include_attachments", sa.Boolean(), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=True),
        sa.Column("date_to", sa.Date(), nullable=True),
        sa.Column("object_key", sa.String(length=180), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_project_export_jobs_project_id"), "project_export_jobs", ["project_id"], unique=False)
    op.create_index(op.f("ix_project_export_jobs_requested_by_user_id"), "project_export_jobs", ["requested_by_user_id"], unique=False)
    op.create_index(op.f("ix_project_export_jobs_status"), "project_export_jobs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_project_export_jobs_status"), table_name="project_export_jobs")
    op.drop_index(op.f("ix_project_export_jobs_requested_by_user_id"), table_name="project_export_jobs")
    op.drop_index(op.f("ix_project_export_jobs_project_id"), table_name="project_export_jobs")
    op.drop_table("project_export_jobs")
