"""Bind admin pairing invitations to a role and validated scope.

Revision ID: 0005_pairing_role_scope
Revises: 0004_processed_telegram_updates
Create Date: 2026-11-21
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "0005_pairing_role_scope"
down_revision: Union[str, None] = "0004_processed_telegram_updates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()

    # Old invitations did not carry an authorization grant. Invalidate them rather
    # than silently promoting a pending account to an administrative role.
    connection.execute(
        sa.text(
            """
            DELETE FROM admin_pairing_tokens
            """
        )
    )
    connection.execute(
        sa.text(
            """
            DELETE FROM users
            WHERE status = 'pending'
              AND id NOT IN (SELECT user_id FROM telegram_accounts)
            """
        )
    )

    op.add_column(
        "admin_pairing_tokens",
        sa.Column("role_id", sa.Uuid(), nullable=False),
    )
    op.add_column(
        "admin_pairing_tokens",
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "admin_pairing_tokens",
        sa.Column("scope_type", sa.String(length=24), nullable=False),
    )
    op.add_column(
        "admin_pairing_tokens",
        sa.Column("scope_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_admin_pairing_role",
        "admin_pairing_tokens",
        "roles",
        ["role_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_admin_pairing_tenant",
        "admin_pairing_tokens",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_admin_pairing_scope",
        "admin_pairing_tokens",
        "(scope_type = 'platform' AND tenant_id IS NULL AND scope_id IS NULL) OR "
        "(scope_type = 'tenant' AND tenant_id IS NOT NULL AND tenant_id = scope_id) OR "
        "(scope_type IN ('organization', 'school', 'class') "
        "AND tenant_id IS NOT NULL AND scope_id IS NOT NULL)",
    )
    op.create_index(
        "uq_admin_pairing_active_target",
        "admin_pairing_tokens",
        ["target_telegram_user_id"],
        unique=True,
        postgresql_where=sa.text(
            "consumed_at IS NULL AND revoked_at IS NULL"
        ),
    )

    permission_id = uuid4()
    role_permission_id = uuid4()
    platform_owner_role_id = connection.execute(
        sa.text(
            """
            SELECT id FROM roles
            WHERE key = 'platform_owner' AND tenant_id IS NULL
            LIMIT 1
            """
        )
    ).scalar_one_or_none()

    existing_permission_id = connection.execute(
        sa.text("SELECT id FROM permissions WHERE key = 'platform:roles:assign'")
    ).scalar_one_or_none()
    if existing_permission_id is None:
        connection.execute(
            sa.text(
                """
                INSERT INTO permissions (id, key, description)
                VALUES (:id, 'platform:roles:assign',
                        'Assign roles to administrators within the actor authority')
                """
            ),
            {"id": permission_id},
        )
        existing_permission_id = permission_id

    if platform_owner_role_id is not None:
        existing_link = connection.execute(
            sa.text(
                """
                SELECT id FROM role_permissions
                WHERE role_id = :role_id AND permission_id = :permission_id
                """
            ),
            {
                "role_id": platform_owner_role_id,
                "permission_id": existing_permission_id,
            },
        ).scalar_one_or_none()
        if existing_link is None:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO role_permissions (id, role_id, permission_id, effect)
                    VALUES (:id, :role_id, :permission_id, 'allow')
                    """
                ),
                {
                    "id": role_permission_id,
                    "role_id": platform_owner_role_id,
                    "permission_id": existing_permission_id,
                },
            )


def downgrade() -> None:
    op.drop_index(
        "uq_admin_pairing_active_target",
        table_name="admin_pairing_tokens",
    )
    op.drop_constraint(
        "ck_admin_pairing_scope",
        "admin_pairing_tokens",
        type_="check",
    )
    op.drop_constraint(
        "fk_admin_pairing_tenant",
        "admin_pairing_tokens",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_admin_pairing_role",
        "admin_pairing_tokens",
        type_="foreignkey",
    )
    op.drop_column("admin_pairing_tokens", "scope_id")
    op.drop_column("admin_pairing_tokens", "scope_type")
    op.drop_column("admin_pairing_tokens", "tenant_id")
    op.drop_column("admin_pairing_tokens", "role_id")

    connection = op.get_bind()
    platform_owner_role_id = connection.execute(
        sa.text(
            """
            SELECT id FROM roles
            WHERE key = 'platform_owner' AND tenant_id IS NULL
            LIMIT 1
            """
        )
    ).scalar_one_or_none()
    permission_id = connection.execute(
        sa.text("SELECT id FROM permissions WHERE key = 'platform:roles:assign'")
    ).scalar_one_or_none()
    if platform_owner_role_id is not None and permission_id is not None:
        connection.execute(
            sa.text(
                """
                DELETE FROM role_permissions
                WHERE role_id = :role_id AND permission_id = :permission_id
                """
            ),
            {
                "role_id": platform_owner_role_id,
                "permission_id": permission_id,
            },
        )
    if permission_id is not None:
        connection.execute(
            sa.text("DELETE FROM permissions WHERE id = :id"),
            {"id": permission_id},
        )
