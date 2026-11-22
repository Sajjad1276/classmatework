"""Add Telegram group links for classes and usage telemetry for reporting.

Revision ID: 0007_control_center
Revises: 0006_tenant_membership_manage
Create Date: 2026-11-22
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "0007_control_center"
down_revision: Union[str, None] = "0006_tenant_membership_manage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ensure_permission(connection, *, key: str, description: str):
    existing = connection.execute(
        sa.text("SELECT id FROM permissions WHERE key = :key"),
        {"key": key},
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    permission_id = uuid4()
    connection.execute(
        sa.text(
            "INSERT INTO permissions (id, key, description) "
            "VALUES (:id, :key, :description)"
        ),
        {"id": permission_id, "key": key, "description": description},
    )
    return permission_id


def _grant_if_missing(connection, *, role_id, permission_id) -> None:
    exists = connection.execute(
        sa.text(
            "SELECT id FROM role_permissions "
            "WHERE role_id = :role_id AND permission_id = :permission_id"
        ),
        {"role_id": role_id, "permission_id": permission_id},
    ).scalar_one_or_none()
    if exists is None:
        connection.execute(
            sa.text(
                "INSERT INTO role_permissions (id, role_id, permission_id, effect) "
                "VALUES (:id, :role_id, :permission_id, 'allow')"
            ),
            {
                "id": uuid4(),
                "role_id": role_id,
                "permission_id": permission_id,
            },
        )


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_class_groups_tenant_id", "class_groups", ["tenant_id", "id"]
    )
    op.create_table(
        "class_telegram_groups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("class_group_id", sa.Uuid(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("chat_username", sa.String(length=64), nullable=True),
        sa.Column("chat_type", sa.String(length=16), nullable=False, server_default="supergroup"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column(
            "connected_by_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("integration_bot_id", sa.BigInteger(), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("class_group_id", name="uq_class_telegram_groups_class"),
        sa.ForeignKeyConstraint(["tenant_id", "class_group_id"], ["class_groups.tenant_id", "class_groups.id"], name="fk_class_telegram_groups_tenant_class", ondelete="RESTRICT"),
        sa.UniqueConstraint("telegram_chat_id", name="uq_class_telegram_groups_chat"),
        sa.CheckConstraint(
            "chat_type IN ('group', 'supergroup')",
            name="ck_class_telegram_groups_chat_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked', 'attention')",
            name="ck_class_telegram_groups_status",
        ),
    )
    op.create_index(
        "ix_class_telegram_groups_tenant_status",
        "class_telegram_groups",
        ["tenant_id", "status"],
    )

    op.create_table(
        "class_group_link_handshakes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("class_group_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_by_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("creator_telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_class_group_link_handshake_hash"),
        sa.ForeignKeyConstraint(["tenant_id", "class_group_id"], ["class_groups.tenant_id", "class_groups.id"], name="fk_class_group_handshakes_tenant_class", ondelete="RESTRICT"),
        sa.CheckConstraint(
            "creator_telegram_user_id > 0",
            name="ck_group_handshake_actor_positive",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_group_handshake_expiry"),
    )
    op.create_index(
        "ix_group_handshakes_class_expiry",
        "class_group_link_handshakes",
        ["class_group_id", "expires_at"],
    )
    op.create_index(
        "uq_class_group_handshake_active", "class_group_link_handshakes", ["class_group_id"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL AND revoked_at IS NULL"),
        sqlite_where=sa.text("consumed_at IS NULL AND revoked_at IS NULL"),
    )

    op.create_table(
        "usage_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("external_event_id", sa.Uuid(), nullable=False),
        sa.Column("source_service", sa.String(length=80), nullable=False),
        sa.Column("event_name", sa.String(length=120), nullable=False),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "actor_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_key_hash", sa.String(length=64), nullable=True),
        sa.Column("aggregate_type", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("aggregate_id", sa.Uuid(), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("external_event_id", name="uq_usage_events_external_id"),
    )
    op.create_index("ix_usage_events_name_time", "usage_events", ["event_name", "occurred_at"])
    op.create_index("ix_usage_events_actor_time", "usage_events", ["actor_key_hash", "occurred_at"])
    op.create_index("ix_usage_events_tenant_time", "usage_events", ["tenant_id", "occurred_at"])

    op.create_table(
        "usage_identities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_key_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("first_source_service", sa.String(length=80), nullable=False),
        sa.Column("last_source_service", sa.String(length=80), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor_key_hash", name="uq_usage_identities_actor_hash"),
        sa.CheckConstraint(
            "status IN ('active', 'inactive', 'blocked')",
            name="ck_usage_identities_status",
        ),
    )
    op.create_index(
        "ix_usage_identities_status_first_seen",
        "usage_identities",
        ["status", "first_seen_at"],
    )

    connection = op.get_bind()
    group_permission_id = _ensure_permission(
        connection,
        key="class:group:manage",
        description="Connect or revoke the Telegram group assigned to a class",
    )
    reports_permission_id = _ensure_permission(
        connection,
        key="platform:reports:read",
        description="View platform-wide user, school, class and usage reports",
    )

    roles = connection.execute(
        sa.text("SELECT id, key, tenant_id FROM roles")
    ).all()
    for role_id, role_key, tenant_id in roles:
        if role_key == "platform_owner" and tenant_id is None:
            _grant_if_missing(connection, role_id=role_id, permission_id=reports_permission_id)
            _grant_if_missing(connection, role_id=role_id, permission_id=group_permission_id)
        elif role_key == "tenant_admin" and tenant_id is not None:
            _grant_if_missing(
                connection,
                role_id=role_id,
                permission_id=group_permission_id,
            )


def downgrade() -> None:
    op.drop_index("ix_usage_identities_status_first_seen", table_name="usage_identities")
    op.drop_table("usage_identities")
    op.drop_index("ix_usage_events_tenant_time", table_name="usage_events")
    op.drop_index("ix_usage_events_actor_time", table_name="usage_events")
    op.drop_index("ix_usage_events_name_time", table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_index("uq_class_group_handshake_active", table_name="class_group_link_handshakes")
    op.drop_index("ix_group_handshakes_class_expiry", table_name="class_group_link_handshakes")
    op.drop_table("class_group_link_handshakes")
    op.drop_index("ix_class_telegram_groups_tenant_status", table_name="class_telegram_groups")
    op.drop_table("class_telegram_groups")
    op.drop_constraint("uq_class_groups_tenant_id", "class_groups", type_="unique")
