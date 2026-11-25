"""Seed platform permissions for direct ClassMate product administration.

Revision ID: 0011_product_admin_permissions
Revises: 0010_archive_restore
Create Date: 2026-11-25
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "0011_product_admin_permissions"
down_revision: Union[str, None] = "0010_archive_restore"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERMISSIONS = (
    ("platform:schools:read", "View schools in the ClassMate product database"),
    ("platform:schools:create", "Create schools in the ClassMate product database"),
    ("platform:schools:manage", "Update and archive ClassMate schools"),
    ("platform:classes:read", "View classes in the ClassMate product database"),
    ("platform:classes:create", "Create classes in the ClassMate product database"),
    ("platform:classes:manage", "Update and archive ClassMate classes"),
    ("platform:subjects:manage", "Manage ClassMate subjects and class assignments"),
    ("platform:timetable:manage", "Manage ClassMate class timetables"),
    ("platform:members:manage", "Manage ClassMate school and class members"),
    ("platform:announcements:manage", "Manage ClassMate announcements"),
    ("platform:groups:manage", "Manage Telegram groups linked to ClassMate classes"),
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
            existing = connection.execute(
                sa.text(
                    "SELECT id FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id"
                ),
                {"role_id": role_id, "permission_id": permission_id},
            ).scalar_one_or_none()
            if existing is None:
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
