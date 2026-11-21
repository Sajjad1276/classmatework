"""Persist Telegram update processing leases for replay protection.

Revision ID: 0004_processed_telegram_updates
Revises: 0003_telegram_admin_identity
Create Date: 2026-11-21
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0004_processed_telegram_updates"
down_revision: Union[str, None] = "0003_telegram_admin_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "processed_telegram_updates",
        sa.Column("update_id", sa.BigInteger(), primary_key=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=160), nullable=True),
        sa.CheckConstraint(
            "status IN ('processing', 'completed', 'failed')",
            name="ck_processed_telegram_updates_status",
        ),
    )
    op.create_index(
        "ix_processed_telegram_updates_lease",
        "processed_telegram_updates",
        ["status", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_processed_telegram_updates_lease",
        table_name="processed_telegram_updates",
    )
    op.drop_table("processed_telegram_updates")
