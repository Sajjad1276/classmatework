"""Create phase-one identity, tenant, academic and audit core.

Revision ID: 0001_phase_one_core
Revises:
Create Date: 2026-11-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_phase_one_core"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("slug", sa.String(length=80), nullable=False, unique=True),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("locale", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=False)

    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_organizations_tenant_slug"),
    )
    op.create_index("ix_organizations_tenant_id", "organizations", ["tenant_id"])

    op.create_table(
        "schools",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("code", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "code", name="uq_schools_tenant_code"),
    )
    op.create_index("ix_schools_tenant_id", "schools", ["tenant_id"])

    op.create_table(
        "academic_years",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("school_id", sa.Uuid(), sa.ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "school_id", "name", name="uq_academic_year_school_name"),
    )
    op.create_index("ix_academic_years_tenant_id", "academic_years", ["tenant_id"])
    op.create_index("ix_academic_years_school_id", "academic_years", ["school_id"])

    op.create_table(
        "class_groups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("school_id", sa.Uuid(), sa.ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("academic_year_id", sa.Uuid(), sa.ForeignKey("academic_years.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("code", sa.String(length=48), nullable=False),
        sa.Column("grade", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "school_id", "academic_year_id", "code", name="uq_class_groups_code"),
    )
    op.create_index("ix_class_groups_tenant_id", "class_groups", ["tenant_id"])
    op.create_index("ix_class_groups_school_id", "class_groups", ["school_id"])
    op.create_index("ix_class_groups_academic_year_id", "class_groups", ["academic_year_id"])

    op.create_table(
        "subjects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("school_id", sa.Uuid(), sa.ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("code", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "school_id", "code", name="uq_subjects_school_code"),
    )
    op.create_index("ix_subjects_tenant_id", "subjects", ["tenant_id"])
    op.create_index("ix_subjects_school_id", "subjects", ["school_id"])

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(length=254), nullable=False, unique=True),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=False)

    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_memberships_tenant_id", "memberships", ["tenant_id"])
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])
    op.create_index("ix_memberships_user_status", "memberships", ["user_id", "status"])
    op.create_index(
        "uq_active_membership_per_user_tenant", "memberships", ["tenant_id", "user_id"],
        unique=True, postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("csrf_hash", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=False)
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    op.create_table(
        "roles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_roles_tenant_id", "roles", ["tenant_id"])
    op.create_index(
        "uq_roles_global_key", "roles", ["key"], unique=True,
        postgresql_where=sa.text("tenant_id IS NULL"),
    )
    op.create_index(
        "uq_roles_tenant_key", "roles", ["tenant_id", "key"], unique=True,
        postgresql_where=sa.text("tenant_id IS NOT NULL"),
    )

    op.create_table(
        "permissions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(length=120), nullable=False, unique=True),
        sa.Column("description", sa.String(length=240), nullable=False),
    )
    op.create_index("ix_permissions_key", "permissions", ["key"], unique=False)

    op.create_table(
        "role_permissions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("role_id", sa.Uuid(), sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("permission_id", sa.Uuid(), sa.ForeignKey("permissions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("effect", sa.String(length=12), nullable=False),
        sa.UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),
        sa.CheckConstraint("effect IN ('allow', 'deny')", name="ck_role_permissions_effect"),
    )
    op.create_index("ix_role_permissions_role_id", "role_permissions", ["role_id"])
    op.create_index("ix_role_permissions_permission_id", "role_permissions", ["permission_id"])

    op.create_table(
        "role_bindings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_id", sa.Uuid(), sa.ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("scope_type", sa.String(length=24), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(scope_type = 'platform' AND tenant_id IS NULL AND scope_id IS NULL) OR "
            "(scope_type <> 'platform' AND tenant_id IS NOT NULL)",
            name="ck_role_bindings_scope",
        ),
    )
    op.create_index("ix_role_bindings_user_id", "role_bindings", ["user_id"])
    op.create_index("ix_role_bindings_role_id", "role_bindings", ["role_id"])
    op.create_index("ix_role_bindings_user_tenant", "role_bindings", ["user_id", "tenant_id"])
    op.create_index("ix_role_bindings_scope", "role_bindings", ["scope_type", "scope_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("aggregate_type", sa.String(length=80), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("event_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_tenant_created", "audit_events", ["tenant_id", "created_at"])
    op.create_index("ix_audit_events_actor_created", "audit_events", ["actor_id", "created_at"])

    op.execute("""
        CREATE OR REPLACE FUNCTION classmatework_reject_audit_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_audit_events_immutable
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION classmatework_reject_audit_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_immutable ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS classmatework_reject_audit_mutation()")
    op.drop_table("audit_events")
    op.drop_table("role_bindings")
    op.drop_table("role_permissions")
    op.drop_index("ix_permissions_key", table_name="permissions")
    op.drop_table("permissions")
    op.drop_index("uq_roles_tenant_key", table_name="roles")
    op.drop_index("uq_roles_global_key", table_name="roles")
    op.drop_index("ix_roles_tenant_id", table_name="roles")
    op.drop_table("roles")
    op.drop_table("sessions")
    op.drop_index("uq_active_membership_per_user_tenant", table_name="memberships")
    op.drop_index("ix_memberships_user_status", table_name="memberships")
    op.drop_index("ix_memberships_user_id", table_name="memberships")
    op.drop_index("ix_memberships_tenant_id", table_name="memberships")
    op.drop_table("memberships")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.drop_table("subjects")
    op.drop_table("class_groups")
    op.drop_table("academic_years")
    op.drop_table("schools")
    op.drop_table("organizations")
    op.drop_index("ix_tenants_slug", table_name="tenants")
    op.drop_table("tenants")
