"""async quote jobs and persisted item matches

Revision ID: b89e31c6d2a4
Revises: 62bba149004f
Create Date: 2026-08-30 10:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b89e31c6d2a4"
down_revision: Union[str, None] = "62bba149004f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "quote_parse_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("quote_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=120), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("parse_method", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["quote_id"], ["quotes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_quote_parse_jobs_project_id"), "quote_parse_jobs", ["project_id"], unique=False)
    op.create_index(op.f("ix_quote_parse_jobs_quote_id"), "quote_parse_jobs", ["quote_id"], unique=False)
    op.create_index(op.f("ix_quote_parse_jobs_status"), "quote_parse_jobs", ["status"], unique=False)

    op.create_table(
        "quote_match_groups",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("canonical_name", sa.String(length=240), nullable=False),
        sa.Column("created_by", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_quote_match_groups_project_id"), "quote_match_groups", ["project_id"], unique=False)

    op.create_table(
        "quote_match_members",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("group_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("quote_id", sa.String(length=36), nullable=False),
        sa.Column("quote_item_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["quote_match_groups.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["quote_id"], ["quotes.id"]),
        sa.ForeignKeyConstraint(["quote_item_id"], ["quote_items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("quote_item_id"),
    )
    op.create_index(op.f("ix_quote_match_members_group_id"), "quote_match_members", ["group_id"], unique=False)
    op.create_index(op.f("ix_quote_match_members_project_id"), "quote_match_members", ["project_id"], unique=False)
    op.create_index(op.f("ix_quote_match_members_quote_id"), "quote_match_members", ["quote_id"], unique=False)
    op.create_index(op.f("ix_quote_match_members_quote_item_id"), "quote_match_members", ["quote_item_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_quote_match_members_quote_item_id"), table_name="quote_match_members")
    op.drop_index(op.f("ix_quote_match_members_quote_id"), table_name="quote_match_members")
    op.drop_index(op.f("ix_quote_match_members_project_id"), table_name="quote_match_members")
    op.drop_index(op.f("ix_quote_match_members_group_id"), table_name="quote_match_members")
    op.drop_table("quote_match_members")
    op.drop_index(op.f("ix_quote_match_groups_project_id"), table_name="quote_match_groups")
    op.drop_table("quote_match_groups")
    op.drop_index(op.f("ix_quote_parse_jobs_status"), table_name="quote_parse_jobs")
    op.drop_index(op.f("ix_quote_parse_jobs_quote_id"), table_name="quote_parse_jobs")
    op.drop_index(op.f("ix_quote_parse_jobs_project_id"), table_name="quote_parse_jobs")
    op.drop_table("quote_parse_jobs")
