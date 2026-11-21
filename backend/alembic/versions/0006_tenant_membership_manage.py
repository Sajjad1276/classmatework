"""Seed tenant membership management permission.

Revision ID: 0006_tenant_membership_manage
Revises: 0005_pairing_role_scope
Create Date: 2026-11-21
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "0006_tenant_membership_manage"
down_revision: Union[str, None] = "0005_pairing_role_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    permission_id = connection.execute(
        sa.text("SELECT id FROM permissions WHERE key = 'membership:manage'")
    ).scalar_one_or_none()
    if permission_id is None:
        permission_id = uuid4()
        connection.execute(
            sa.text(
                """
                INSERT INTO permissions (id, key, description)
                VALUES (:id, 'membership:manage',
                        'Suspend or manage tenant memberships within the actor authority')
                """
            ),
            {"id": permission_id},
        )

    tenant_admin_roles = connection.execute(
        sa.text(
            """
            SELECT id FROM roles
            WHERE tenant_id IS NOT NULL AND key = 'tenant_admin'
            """
        )
    ).scalars().all()

    for role_id in tenant_admin_roles:
        existing = connection.execute(
            sa.text(
                """
                SELECT id FROM role_permissions
                WHERE role_id = :role_id AND permission_id = :permission_id
                """
            ),
            {"role_id": role_id, "permission_id": permission_id},
        ).scalar_one_or_none()
        if existing is None:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO role_permissions (id, role_id, permission_id, effect)
                    VALUES (:id, :role_id, :permission_id, 'allow')
                    """
                ),
                {
                    "id": uuid4(),
                    "role_id": role_id,
                    "permission_id": permission_id,
                },
            )


def downgrade() -> None:
    # Retain the permission record/grants so a downgrade does not remove
    # permissions that may have been deliberately assigned after deployment.
    pass
