"""Replace browser sessions with Telegram admin identities.

Revision ID: 0003_telegram_admin_identity
Revises: 0002_scope_tenant_integrity
Create Date: 2026-11-21
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0003_telegram_admin_identity"
down_revision: Union[str, None] = "0002_scope_tenant_integrity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Email/password remain optional contact or legacy metadata, not admin credentials.
    op.alter_column("users", "email", existing_type=sa.String(length=254), nullable=True)
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=True)

    # Browser sessions and their CSRF material are not part of the Telegram-only product.
    op.drop_table("sessions")

    op.create_table(
        "telegram_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=128), nullable=True),
        sa.Column("last_name", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unlinked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_telegram_accounts_user"),
        sa.UniqueConstraint(
            "telegram_user_id", name="uq_telegram_accounts_telegram_user"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'unlinked', 'blocked')",
            name="ck_telegram_accounts_status",
        ),
    )
    op.create_index(
        "ix_telegram_accounts_status", "telegram_accounts", ["status"]
    )

    op.create_table(
        "admin_pairing_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_by_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_admin_pairing_token_hash"),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_admin_pairing_expiry"
        ),
    )
    op.create_index(
        "ix_admin_pairing_tokens_user_id", "admin_pairing_tokens", ["user_id"]
    )
    op.create_index(
        "ix_admin_pairing_target_expiry",
        "admin_pairing_tokens",
        ["target_telegram_user_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_admin_pairing_target_expiry", table_name="admin_pairing_tokens"
    )
    op.drop_index(
        "ix_admin_pairing_tokens_user_id", table_name="admin_pairing_tokens"
    )
    op.drop_table("admin_pairing_tokens")
    op.drop_index("ix_telegram_accounts_status", table_name="telegram_accounts")
    op.drop_table("telegram_accounts")

    # Downgrade can only restore the old NOT NULL assumptions when no Telegram-only
    # account has been created without an email/password yet.
    op.alter_column("users", "email", existing_type=sa.String(length=254), nullable=False)
    op.alter_column("users", "password_hash", existing_type=sa.Text(), nullable=False)

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("csrf_hash", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=False)
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
