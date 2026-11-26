"""Store stable displayable ClassMate class join codes in Work DB.

Revision ID: 0013_class_join_code_secrets
Revises: 0012_product_record_review
Create Date: 2026-11-26
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0013_class_join_code_secrets"
down_revision: Union[str, None] = "0012_product_record_review"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "class_join_code_secrets",
        sa.Column("class_id", sa.BigInteger(), primary_key=True),
        sa.Column("join_code", sa.String(length=64), nullable=False),
        sa.Column("join_code_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "join_code_hash",
            name="uq_class_join_code_secrets_hash",
        ),
    )
    op.create_index(
        "ix_class_join_code_secrets_updated_at",
        "class_join_code_secrets",
        ["updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_class_join_code_secrets_updated_at",
        table_name="class_join_code_secrets",
    )
    op.drop_table("class_join_code_secrets")
