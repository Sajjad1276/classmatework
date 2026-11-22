"""Add edit and archive permissions for school structure lifecycle.

Revision ID: 0009_structure_lifecycle
Revises: 0008_class_subjects
Create Date: 2026-11-22
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "0009_structure_lifecycle"
down_revision: Union[str, None] = "0008_class_subjects"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERMISSION_DESCRIPTIONS = {
    "school:manage": "Edit or archive a school",
    "academic_year:manage": "Edit or archive an academic year",
    "class:manage": "Edit or archive a class",
    "subject:manage": "Edit or archive a subject",
}


def upgrade() -> None:
    connection = op.get_bind()
    permission_ids = {}
    for key, description in _PERMISSION_DESCRIPTIONS.items():
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
        permission_ids[key] = permission_id

    roles = connection.execute(
        sa.text("SELECT id, key, tenant_id FROM roles")
    ).all()
    for role_id, role_key, tenant_id in roles:
        if not (
            (role_key == "platform_owner" and tenant_id is None)
            or (role_key == "tenant_admin" and tenant_id is not None)
        ):
            continue
        for permission_id in permission_ids.values():
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
    permission_ids = connection.execute(
        sa.text("SELECT id FROM permissions WHERE key IN :keys").bindparams(
            sa.bindparam("keys", expanding=True)
        ),
        {"keys": list(_PERMISSION_DESCRIPTIONS)},
    ).scalars().all()
    if permission_ids:
        connection.execute(
            sa.text("DELETE FROM role_permissions WHERE permission_id IN :ids").bindparams(
                sa.bindparam("ids", expanding=True)
            ),
            {"ids": permission_ids},
        )
    connection.execute(
        sa.text("DELETE FROM permissions WHERE key IN :keys").bindparams(
            sa.bindparam("keys", expanding=True)
        ),
        {"keys": list(_PERMISSION_DESCRIPTIONS)},
    )
