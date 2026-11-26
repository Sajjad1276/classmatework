from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(180))
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Tehran", nullable=False)
    locale: Mapped[str] = mapped_column(String(12), default="fa-IR", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_organizations_tenant_slug"),
        UniqueConstraint("tenant_id", "id", name="uq_organizations_tenant_id_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(180))
    slug: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class School(Base):
    __tablename__ = "schools"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_schools_tenant_code"),
        UniqueConstraint("tenant_id", "id", name="uq_schools_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "organization_id"],
            ["organizations.tenant_id", "organizations.id"],
            name="fk_schools_tenant_organization",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), index=True
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(180))
    code: Mapped[str] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(24), default="setup", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AcademicYear(Base):
    __tablename__ = "academic_years"
    __table_args__ = (
        UniqueConstraint("tenant_id", "school_id", "name", name="uq_academic_year_school_name"),
        UniqueConstraint("tenant_id", "school_id", "id", name="uq_academic_years_tenant_school_id"),
        ForeignKeyConstraint(
            ["tenant_id", "school_id"],
            ["schools.tenant_id", "schools.id"],
            name="fk_academic_years_tenant_school",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), index=True
    )
    school_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("schools.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(64))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(24), default="planned", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ClassGroup(Base):
    __tablename__ = "class_groups"
    __table_args__ = (
        UniqueConstraint("tenant_id", "school_id", "academic_year_id", "code", name="uq_class_groups_code"),
        UniqueConstraint("tenant_id", "id", name="uq_class_groups_tenant_id"),
        UniqueConstraint("tenant_id", "school_id", "id", name="uq_class_groups_tenant_school_id"),
        ForeignKeyConstraint(
            ["tenant_id", "school_id"],
            ["schools.tenant_id", "schools.id"],
            name="fk_class_groups_tenant_school",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "school_id", "academic_year_id"],
            ["academic_years.tenant_id", "academic_years.school_id", "academic_years.id"],
            name="fk_class_groups_tenant_school_year",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), index=True
    )
    school_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("schools.id", ondelete="RESTRICT"), index=True
    )
    academic_year_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("academic_years.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(48))
    grade: Mapped[str] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)



class ClassTelegramGroup(Base):
    __tablename__ = "class_telegram_groups"
    __table_args__ = (
        UniqueConstraint("class_group_id", name="uq_class_telegram_groups_class"),
        UniqueConstraint("telegram_chat_id", name="uq_class_telegram_groups_chat"),
        ForeignKeyConstraint(
            ["tenant_id", "class_group_id"],
            ["class_groups.tenant_id", "class_groups.id"],
            name="fk_class_telegram_groups_tenant_class",
            ondelete="RESTRICT",
        ),
        CheckConstraint("chat_type IN ('group', 'supergroup')", name="ck_class_telegram_groups_chat_type"),
        CheckConstraint("status IN ('active', 'revoked', 'attention')", name="ck_class_telegram_groups_status"),
        Index("ix_class_telegram_groups_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False)
    class_group_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    chat_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chat_type: Mapped[str] = mapped_column(String(16), default="supergroup", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    connected_by_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    integration_bot_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ClassGroupLinkHandshake(Base):
    __tablename__ = "class_group_link_handshakes"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_class_group_link_handshake_hash"),
        ForeignKeyConstraint(
            ["tenant_id", "class_group_id"],
            ["class_groups.tenant_id", "class_groups.id"],
            name="fk_class_group_handshakes_tenant_class",
            ondelete="RESTRICT",
        ),
        CheckConstraint("creator_telegram_user_id > 0", name="ck_group_handshake_actor_positive"),
        CheckConstraint("expires_at > created_at", name="ck_group_handshake_expiry"),
        Index(
            "uq_class_group_handshake_active",
            "class_group_id",
            unique=True,
            postgresql_where=text("consumed_at IS NULL AND revoked_at IS NULL"),
            sqlite_where=text("consumed_at IS NULL AND revoked_at IS NULL"),
        ),
        Index("ix_group_handshakes_class_expiry", "class_group_id", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False)
    class_group_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_by_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    creator_telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        UniqueConstraint("external_event_id", name="uq_usage_events_external_id"),
        Index("ix_usage_events_name_time", "event_name", "occurred_at"),
        Index("ix_usage_events_actor_time", "actor_key_hash", "occurred_at"),
        Index("ix_usage_events_tenant_time", "tenant_id", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    external_event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_service: Mapped[str] = mapped_column(String(80), nullable=False)
    event_name: Mapped[str] = mapped_column(String(120), nullable=False)
    tenant_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=True)
    actor_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    aggregate_type: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    aggregate_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UsageIdentity(Base):
    __tablename__ = "usage_identities"
    __table_args__ = (
        UniqueConstraint("actor_key_hash", name="uq_usage_identities_actor_hash"),
        CheckConstraint(
            "status IN ('active', 'inactive', 'blocked')",
            name="ck_usage_identities_status",
        ),
        Index("ix_usage_identities_status_first_seen", "status", "first_seen_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    actor_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    first_source_service: Mapped[str] = mapped_column(String(80), nullable=False)
    last_source_service: Mapped[str] = mapped_column(String(80), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Subject(Base):
    __tablename__ = "subjects"
    __table_args__ = (
        UniqueConstraint("tenant_id", "school_id", "code", name="uq_subjects_school_code"),
        UniqueConstraint("tenant_id", "school_id", "id", name="uq_subjects_tenant_school_id"),
        ForeignKeyConstraint(
            ["tenant_id", "school_id"],
            ["schools.tenant_id", "schools.id"],
            name="fk_subjects_tenant_school",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), index=True
    )
    school_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("schools.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    code: Mapped[str] = mapped_column(String(48))
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ClassSubject(Base):
    __tablename__ = "class_subjects"
    __table_args__ = (
        UniqueConstraint(
            "class_group_id",
            "subject_id",
            name="uq_class_subjects_class_subject",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "school_id", "class_group_id"],
            ["class_groups.tenant_id", "class_groups.school_id", "class_groups.id"],
            name="fk_class_subjects_tenant_school_class",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "school_id", "subject_id"],
            ["subjects.tenant_id", "subjects.school_id", "subjects.id"],
            name="fk_class_subjects_tenant_school_subject",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive', 'suspended')",
            name="ck_class_subjects_status",
        ),
        Index(
            "ix_class_subjects_tenant_school_status",
            "tenant_id",
            "school_id",
            "status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    school_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    class_group_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    assigned_by_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str | None] = mapped_column(
        String(254), unique=True, index=True, nullable=True
    )
    display_name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TenantMembership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        Index("ix_memberships_user_status", "user_id", "status"),
        Index(
            "uq_active_membership_per_user_tenant",
            "tenant_id",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TelegramAccount(Base):
    __tablename__ = "telegram_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_telegram_accounts_user"),
        UniqueConstraint("telegram_user_id", name="uq_telegram_accounts_telegram_user"),
        CheckConstraint("status IN ('active', 'unlinked', 'blocked')", name="ck_telegram_accounts_status"),
        Index("ix_telegram_accounts_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    unlinked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AdminPairingToken(Base):
    __tablename__ = "admin_pairing_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_admin_pairing_token_hash"),
        CheckConstraint("expires_at > created_at", name="ck_admin_pairing_expiry"),
        CheckConstraint("target_telegram_user_id > 0", name="ck_admin_pairing_target_positive"),
        CheckConstraint(
            "(scope_type = 'platform' AND tenant_id IS NULL AND scope_id IS NULL) OR "
            "(scope_type = 'tenant' AND tenant_id IS NOT NULL AND tenant_id = scope_id) OR "
            "(scope_type IN ('organization', 'school', 'class') "
            "AND tenant_id IS NOT NULL AND scope_id IS NOT NULL)",
            name="ck_admin_pairing_scope",
        ),
        Index("ix_admin_pairing_target_expiry", "target_telegram_user_id", "expires_at"),
        Index(
            "uq_admin_pairing_active_target",
            "target_telegram_user_id",
            unique=True,
            postgresql_where=text("consumed_at IS NULL AND revoked_at IS NULL"),
            sqlite_where=text("consumed_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    target_telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    role_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False
    )
    tenant_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=True
    )
    scope_type: Mapped[str] = mapped_column(String(24), nullable=False)
    scope_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_by_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)



class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        Index(
            "uq_roles_global_key", "key", unique=True,
            postgresql_where=text("tenant_id IS NULL"),
            sqlite_where=text("tenant_id IS NULL"),
        ),
        Index(
            "uq_roles_tenant_key", "tenant_id", "key", unique=True,
            postgresql_where=text("tenant_id IS NOT NULL"),
            sqlite_where=text("tenant_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    key: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(120))
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(240), default="", nullable=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),
        CheckConstraint("effect IN ('allow', 'deny')", name="ck_role_permissions_effect"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    role_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), index=True
    )
    permission_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("permissions.id", ondelete="RESTRICT"), index=True
    )
    effect: Mapped[str] = mapped_column(String(12), default="allow", nullable=False)


class RoleBinding(Base):
    __tablename__ = "role_bindings"
    __table_args__ = (
        Index("ix_role_bindings_user_tenant", "user_id", "tenant_id"),
        Index("ix_role_bindings_scope", "scope_type", "scope_id"),
        CheckConstraint(
            "(scope_type = 'platform' AND tenant_id IS NULL AND scope_id IS NULL) OR "
            "(scope_type = 'tenant' AND tenant_id = scope_id) OR "
            "(scope_type IN ('organization', 'school', 'class') "
            "AND tenant_id IS NOT NULL AND scope_id IS NOT NULL)",
            name="ck_role_bindings_scope",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT"), index=True
    )
    tenant_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=True
    )
    scope_type: Mapped[str] = mapped_column(String(24))
    scope_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)



class ClassJoinCodeSecret(Base):
    """Admin-side clear-text copy for displaying a stable class join code."""

    __tablename__ = "class_join_code_secrets"
    __table_args__ = (
        UniqueConstraint("join_code_hash", name="uq_class_join_code_secrets_hash"),
    )

    class_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    join_code: Mapped[str] = mapped_column(String(64), nullable=False)
    join_code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ProcessedTelegramUpdate(Base):
    __tablename__ = "processed_telegram_updates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing', 'completed', 'failed')",
            name="ck_processed_telegram_updates_status",
        ),
        Index("ix_processed_telegram_updates_lease", "status", "lease_expires_at"),
    )

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="processing", nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(160), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_tenant_created", "tenant_id", "created_at"),
        Index("ix_audit_events_actor_created", "actor_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=True
    )
    actor_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    aggregate_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def _reject_audit_mutation(_mapper, _connection, _target) -> None:
    raise RuntimeError("AuditEvent rows are append-only and cannot be changed or deleted.")


event.listen(AuditEvent, "before_update", _reject_audit_mutation)
event.listen(AuditEvent, "before_delete", _reject_audit_mutation)
