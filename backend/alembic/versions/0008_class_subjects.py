"""Add class-to-subject assignments.

Revision ID: 0008_class_subjects
Revises: 0007_control_center
Create Date: 2026-11-22
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0008_class_subjects"
down_revision: Union[str, None] = "0007_control_center"
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
            {"id": uuid4(), "role_id": role_id, "permission_id": permission_id},
        )


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_class_groups_tenant_school_id",
        "class_groups",
        ["tenant_id", "school_id", "id"],
    )
    op.create_unique_constraint(
        "uq_subjects_tenant_school_id",
        "subjects",
        ["tenant_id", "school_id", "id"],
    )
    op.create_table(
        "class_subjects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("class_group_id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column(
            "assigned_by_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "class_group_id",
            "subject_id",
            name="uq_class_subjects_class_subject",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "school_id", "class_group_id"],
            ["class_groups.tenant_id", "class_groups.school_id", "class_groups.id"],
            name="fk_class_subjects_tenant_school_class",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "school_id", "subject_id"],
            ["subjects.tenant_id", "subjects.school_id", "subjects.id"],
            name="fk_class_subjects_tenant_school_subject",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_class_subjects_status",
        ),
    )
    op.create_index(
        "ix_class_subjects_tenant_school_status",
        "class_subjects",
        ["tenant_id", "school_id", "status"],
    )

    connection = op.get_bind()
    permission_id = _ensure_permission(
        connection,
        key="class:subjects:manage",
        description="Assign or remove subjects for a class",
    )
    roles = connection.execute(
        sa.text("SELECT id, key, tenant_id FROM roles")
    ).all()
    for role_id, role_key, tenant_id in roles:
        if (role_key == "platform_owner" and tenant_id is None) or (
            role_key == "tenant_admin" and tenant_id is not None
        ):
            _grant_if_missing(
                connection,
                role_id=role_id,
                permission_id=permission_id,
            )


def downgrade() -> None:
    op.drop_index("ix_class_subjects_tenant_school_status", table_name="class_subjects")
    op.drop_table("class_subjects")
    op.drop_constraint(
        "uq_subjects_tenant_school_id",
        "subjects",
        type_="unique",
    )
    op.drop_constraint(
        "uq_class_groups_tenant_school_id",
        "class_groups",
        type_="unique",
    )
