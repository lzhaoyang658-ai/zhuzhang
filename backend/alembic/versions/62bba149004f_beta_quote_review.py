"""beta quote review and OCR metadata

Revision ID: 62bba149004f
Revises: 35f310b7eb14
Create Date: 2026-08-30 03:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "62bba149004f"
down_revision: Union[str, None] = "35f310b7eb14"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("quotes") as batch:
        batch.add_column(sa.Column("input_type", sa.String(length=30), nullable=False, server_default="spreadsheet"))
        batch.add_column(sa.Column("parse_method", sa.String(length=40), nullable=False, server_default="deterministic_table"))
        batch.add_column(sa.Column("parser_version", sa.String(length=40), nullable=False, server_default="beta-1"))
        batch.add_column(sa.Column("page_count", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("warnings", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("error_message", sa.Text(), nullable=True))
    with op.batch_alter_table("quote_items") as batch:
        batch.add_column(sa.Column("source_excerpt", sa.Text(), nullable=True))
        batch.add_column(sa.Column("field_confidences", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("material_info", sa.Text(), nullable=True))
        batch.add_column(sa.Column("craft_notes", sa.Text(), nullable=True))
    op.create_table(
        "quote_corrections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("quote_id", sa.String(length=36), nullable=False),
        sa.Column("quote_item_id", sa.String(length=36), nullable=False),
        sa.Column("field_name", sa.String(length=60), nullable=False),
        sa.Column("previous_value", sa.Text(), nullable=True),
        sa.Column("corrected_value", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["quote_id"], ["quotes.id"]),
        sa.ForeignKeyConstraint(["quote_item_id"], ["quote_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_quote_corrections_project_id"), "quote_corrections", ["project_id"], unique=False)
    op.create_index(op.f("ix_quote_corrections_quote_id"), "quote_corrections", ["quote_id"], unique=False)
    op.create_index(op.f("ix_quote_corrections_quote_item_id"), "quote_corrections", ["quote_item_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_quote_corrections_quote_item_id"), table_name="quote_corrections")
    op.drop_index(op.f("ix_quote_corrections_quote_id"), table_name="quote_corrections")
    op.drop_index(op.f("ix_quote_corrections_project_id"), table_name="quote_corrections")
    op.drop_table("quote_corrections")
    with op.batch_alter_table("quote_items") as batch:
        batch.drop_column("craft_notes")
        batch.drop_column("material_info")
        batch.drop_column("field_confidences")
        batch.drop_column("source_excerpt")
    with op.batch_alter_table("quotes") as batch:
        batch.drop_column("error_message")
        batch.drop_column("warnings")
        batch.drop_column("page_count")
        batch.drop_column("parser_version")
        batch.drop_column("parse_method")
        batch.drop_column("input_type")
