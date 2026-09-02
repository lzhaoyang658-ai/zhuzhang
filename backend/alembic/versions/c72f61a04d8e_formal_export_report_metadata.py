"""formal export report metadata

Revision ID: c72f61a04d8e
Revises: b4e87f19c632
Create Date: 2026-08-31 14:10:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c72f61a04d8e"
down_revision: Union[str, None] = "b4e87f19c632"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("project_export_jobs") as batch_op:
        batch_op.add_column(sa.Column("artifact_sha256", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("storage_backend", sa.String(length=30), nullable=False, server_default="local"))
        batch_op.add_column(sa.Column("report_version", sa.String(length=30), nullable=False, server_default="formal-v2"))
        batch_op.add_column(sa.Column("report_page_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("project_export_jobs") as batch_op:
        batch_op.drop_column("report_page_count")
        batch_op.drop_column("report_version")
        batch_op.drop_column("storage_backend")
        batch_op.drop_column("artifact_sha256")
