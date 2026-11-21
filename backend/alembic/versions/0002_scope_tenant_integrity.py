"""Enforce scope and cross-tenant academic integrity.

Revision ID: 0002_scope_tenant_integrity
Revises: 0001_phase_one_core
Create Date: 2026-11-21
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002_scope_tenant_integrity"
down_revision: Union[str, None] = "0001_phase_one_core"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add candidate keys used by composite foreign keys.
    op.create_unique_constraint(
        "uq_organizations_tenant_id_id", "organizations", ["tenant_id", "id"]
    )
    op.create_unique_constraint(
        "uq_schools_tenant_id_id", "schools", ["tenant_id", "id"]
    )
    op.create_unique_constraint(
        "uq_academic_years_tenant_school_id",
        "academic_years",
        ["tenant_id", "school_id", "id"],
    )

    # Ensure related educational records always belong to the same tenant/school.
    op.create_foreign_key(
        "fk_schools_tenant_organization",
        "schools",
        "organizations",
        ["tenant_id", "organization_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_academic_years_tenant_school",
        "academic_years",
        "schools",
        ["tenant_id", "school_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_class_groups_tenant_school",
        "class_groups",
        "schools",
        ["tenant_id", "school_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_class_groups_tenant_school_year",
        "class_groups",
        "academic_years",
        ["tenant_id", "school_id", "academic_year_id"],
        ["tenant_id", "school_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_subjects_tenant_school",
        "subjects",
        "schools",
        ["tenant_id", "school_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )

    # Align the database constraint with the stricter ORM model.
    op.drop_constraint("ck_role_bindings_scope", "role_bindings", type_="check")
    op.create_check_constraint(
        "ck_role_bindings_scope",
        "role_bindings",
        "(scope_type = 'platform' AND tenant_id IS NULL AND scope_id IS NULL) OR "
        "(scope_type = 'tenant' AND tenant_id = scope_id) OR "
        "(scope_type IN ('organization', 'school', 'class') "
        "AND tenant_id IS NOT NULL AND scope_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_role_bindings_scope", "role_bindings", type_="check"
    )
    op.create_check_constraint(
        "ck_role_bindings_scope",
        "role_bindings",
        "(scope_type = 'platform' AND tenant_id IS NULL AND scope_id IS NULL) OR "
        "(scope_type <> 'platform' AND tenant_id IS NOT NULL)",
    )

    op.drop_constraint("fk_subjects_tenant_school", "subjects", type_="foreignkey")
    op.drop_constraint(
        "fk_class_groups_tenant_school_year", "class_groups", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_class_groups_tenant_school", "class_groups", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_academic_years_tenant_school", "academic_years", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_schools_tenant_organization", "schools", type_="foreignkey"
    )

    op.drop_constraint(
        "uq_academic_years_tenant_school_id", "academic_years", type_="unique"
    )
    op.drop_constraint("uq_schools_tenant_id_id", "schools", type_="unique")
    op.drop_constraint(
        "uq_organizations_tenant_id_id", "organizations", type_="unique"
    )
