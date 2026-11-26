"""Add suspended state to archived class-subject assignments.

Revision ID: 0010_archive_restore
Revises: 0009_structure_lifecycle
Create Date: 2026-11-22
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0010_archive_restore"
down_revision: Union[str, None] = "0009_structure_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_class_subjects_status",
        "class_subjects",
        type_="check",
    )
    op.create_check_constraint(
        "ck_class_subjects_status",
        "class_subjects",
        "status IN ('active', 'inactive', 'suspended')",
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE class_subjects SET status = 'inactive' WHERE status = 'suspended'"
        )
    )
    op.drop_constraint(
        "ck_class_subjects_status",
        "class_subjects",
        type_="check",
    )
    op.create_check_constraint(
        "ck_class_subjects_status",
        "class_subjects",
        "status IN ('active', 'inactive')",
    )
