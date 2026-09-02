"""persist payment idempotency keys

Revision ID: a61d7b24e9f3
Revises: e83b4d9a6f12
Create Date: 2026-08-31 18:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a61d7b24e9f3"
down_revision: Union[str, None] = "e83b4d9a6f12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payment_records", sa.Column("idempotency_key", sa.String(length=80), nullable=True))
    op.create_index(
        "uq_payment_records_idempotency_key",
        "payment_records",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_payment_records_idempotency_key", table_name="payment_records")
    op.drop_column("payment_records", "idempotency_key")
