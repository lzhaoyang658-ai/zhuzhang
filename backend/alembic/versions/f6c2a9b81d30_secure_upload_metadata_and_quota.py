"""add secure upload metadata and project source quotas

Revision ID: f6c2a9b81d30
Revises: d4a8b7c91e20
Create Date: 2026-09-02 14:30:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f6c2a9b81d30"
down_revision: Union[str, None] = "d4a8b7c91e20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SCAN_STATUSES = "'legacy_unscanned', 'pending', 'clean', 'skipped', 'infected', 'error'"


def upgrade() -> None:
    bind = op.get_bind()
    invalid_evidence = bind.execute(sa.text("""
        SELECT id, project_id, size_bytes
        FROM evidence
        WHERE size_bytes < 0
    """)).mappings().all()
    if invalid_evidence:
        raise RuntimeError(
            f"存在负数附件大小，迁移已中止: {[dict(row) for row in invalid_evidence]}"
        )

    # These parent tables are referenced by several foreign keys. Adding each
    # constraint inline avoids SQLite's batch-table replacement, which cannot
    # drop a referenced table while PRAGMA foreign_keys is enabled.
    op.add_column(
        "projects",
        sa.Column(
            "source_file_count",
            sa.Integer(),
            sa.CheckConstraint(
                "source_file_count >= 0",
                name="ck_projects_source_file_count_nonnegative",
            ),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "source_bytes",
            sa.Integer(),
            sa.CheckConstraint(
                "source_bytes >= 0",
                name="ck_projects_source_bytes_nonnegative",
            ),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    op.add_column(
        "quotes",
        sa.Column(
            "source_size_bytes",
            sa.Integer(),
            sa.CheckConstraint(
                "source_size_bytes >= 0",
                name="ck_quotes_source_size_bytes_nonnegative",
            ),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "quotes",
        sa.Column(
            "source_sha256",
            sa.String(length=64),
            sa.CheckConstraint(
                "source_sha256 IS NULL OR length(source_sha256) = 64",
                name="ck_quotes_source_sha256_length",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "quotes",
        sa.Column(
            "source_mime_type",
            sa.String(length=100),
            nullable=True,
        ),
    )
    op.add_column(
        "quotes",
        sa.Column(
            "scan_status",
            sa.String(length=24),
            sa.CheckConstraint(
                f"scan_status IN ({SCAN_STATUSES})",
                name="ck_quotes_scan_status",
            ),
            nullable=False,
            server_default="legacy_unscanned",
        ),
    )
    op.add_column(
        "quotes",
        sa.Column(
            "scanned_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Evidence has no dependent tables, so its historical size constraint can
    # be added safely via batch replacement before adding inline constraints.
    # Doing this first also avoids SQLite mis-reflecting inline CHECK clauses.
    with op.batch_alter_table("evidence") as batch_op:
        batch_op.create_check_constraint(
            "ck_evidence_size_bytes_nonnegative",
            "size_bytes >= 0",
        )

    op.add_column(
        "evidence",
        sa.Column(
            "sha256",
            sa.String(length=64),
            sa.CheckConstraint(
                "sha256 IS NULL OR length(sha256) = 64",
                name="ck_evidence_sha256_length",
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "evidence",
        sa.Column(
            "scan_status",
            sa.String(length=24),
            sa.CheckConstraint(
                f"scan_status IN ({SCAN_STATUSES})",
                name="ck_evidence_scan_status",
            ),
            nullable=False,
            server_default="legacy_unscanned",
        ),
    )
    op.add_column(
        "evidence",
        sa.Column(
            "scanned_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Historical quote source sizes were not recorded. Keep their size at zero
    # instead of fabricating a value; evidence sizes can be backfilled exactly.
    bind.execute(sa.text("""
        UPDATE projects
        SET source_file_count =
                (SELECT COUNT(*) FROM quotes WHERE quotes.project_id = projects.id)
                +
                (SELECT COUNT(*) FROM evidence WHERE evidence.project_id = projects.id),
            source_bytes = COALESCE(
                (SELECT SUM(evidence.size_bytes)
                 FROM evidence
                 WHERE evidence.project_id = projects.id),
                0
            )
    """))


def downgrade() -> None:
    op.drop_column("evidence", "scanned_at")
    op.drop_column("evidence", "scan_status")
    op.drop_column("evidence", "sha256")
    with op.batch_alter_table("evidence") as batch_op:
        batch_op.drop_constraint("ck_evidence_size_bytes_nonnegative", type_="check")

    op.drop_column("quotes", "scanned_at")
    op.drop_column("quotes", "scan_status")
    op.drop_column("quotes", "source_mime_type")
    op.drop_column("quotes", "source_sha256")
    op.drop_column("quotes", "source_size_bytes")

    op.drop_column("projects", "source_bytes")
    op.drop_column("projects", "source_file_count")
