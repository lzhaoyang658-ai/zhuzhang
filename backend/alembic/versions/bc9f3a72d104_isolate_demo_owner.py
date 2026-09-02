"""isolate the local demo owner from real projects

Revision ID: bc9f3a72d104
Revises: a61d7b24e9f3
Create Date: 2026-09-02 09:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "bc9f3a72d104"
down_revision: Union[str, None] = "a61d7b24e9f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Preserve a demo-only project, but remove the hidden demo membership from
    # every project that already has another active owner.
    op.execute(sa.text("""
        UPDATE project_memberships
        SET status = 'revoked'
        WHERE user_id = 'demo-owner'
          AND EXISTS (
              SELECT 1
              FROM project_memberships AS real_owner
              WHERE real_owner.project_id = project_memberships.project_id
                AND real_owner.user_id <> 'demo-owner'
                AND real_owner.role = 'owner'
                AND real_owner.status = 'active'
          )
    """))
    op.execute(sa.text("""
        UPDATE login_sessions
        SET revoked_at = CURRENT_TIMESTAMP
        WHERE user_id = 'demo-owner' AND revoked_at IS NULL
    """))
    op.execute(sa.text("""
        UPDATE login_challenges
        SET status = 'expired'
        WHERE lower(email) = 'owner@example.local' AND status = 'pending'
    """))
    op.execute(sa.text("""
        UPDATE users
        SET status = 'disabled'
        WHERE id = 'demo-owner'
          AND NOT EXISTS (
              SELECT 1
              FROM project_memberships
              WHERE user_id = 'demo-owner' AND status = 'active'
          )
    """))


def downgrade() -> None:
    # Removed access grants cannot be reconstructed safely.
    pass
