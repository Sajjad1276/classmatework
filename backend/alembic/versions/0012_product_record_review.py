"""Seed global permissions for the ClassMate educational record queue.

Revision ID: 0012_product_record_review
Revises: 0011_product_admin_permissions
Create Date: 2026-11-25
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "0012_product_record_review"
down_revision: Union[str, None] = "0011_product_admin_permissions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERMISSIONS = (
    ("platform:records:read", "View pending and historical ClassMate learning records"),
    ("platform:records:review", "Approve or reject ClassMate homework, exams and reports"),
    ("platform:attendance:manage", "Manage ClassMate class attendance"),
    ("platform:grades:manage", "Manage ClassMate student grades"),
    ("platform:submissions:manage", "Review ClassMate homework submissions"),
    ("platform:polls:manage", "Manage ClassMate class polls"),
)


def upgrade() -> None:
    connection = op.get_bind()
    owners = connection.execute(
        sa.text("SELECT id FROM roles WHERE key = 'platform_owner' AND tenant_id IS NULL")
    ).scalars().all()

    for key, description in _PERMISSIONS:
        permission_id = connection.execute(
            sa.text("SELECT id FROM permissions WHERE key = :key"),
            {"key": key},
        ).scalar_one_or_none()
        if permission_id is None:
            permission_id = uuid4()
            connection.execute(
                sa.text(
                    "INSERT INTO permissions (id, key, description) "
                    "VALUES (:id, :key, :description)"
                ),
                {"id": permission_id, "key": key, "description": description},
            )

        for role_id in owners:
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


def downgrade() -> None:
    connection = op.get_bind()
    for key, _ in _PERMISSIONS:
        permission_id = connection.execute(
            sa.text("SELECT id FROM permissions WHERE key = :key"),
            {"key": key},
        ).scalar_one_or_none()
        if permission_id is None:
            continue
        connection.execute(
            sa.text("DELETE FROM role_permissions WHERE permission_id = :permission_id"),
            {"permission_id": permission_id},
        )
        connection.execute(
            sa.text("DELETE FROM permissions WHERE id = :permission_id"),
            {"permission_id": permission_id},
        )
