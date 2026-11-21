import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.db.runtime import create_database_engine


@pytest.mark.asyncio
async def test_postgres_rejects_cross_tenant_academic_relationships() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is not configured.")

    engine = create_database_engine(database_url)
    tenant_a, tenant_b = uuid4(), uuid4()
    org_b = uuid4()
    school_a, school_b = uuid4(), uuid4()
    year_b = uuid4()
    now = datetime.now(timezone.utc)

    async with engine.connect() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    """
                    INSERT INTO tenants
                        (id, slug, name, status, timezone, locale, created_at)
                    VALUES
                        (CAST(:a AS uuid), 'integrity-a', 'Integrity A', 'active',
                         'Asia/Tehran', 'fa-IR', :now),
                        (CAST(:b AS uuid), 'integrity-b', 'Integrity B', 'active',
                         'Asia/Tehran', 'fa-IR', :now)
                    """
                ),
                {"a": str(tenant_a), "b": str(tenant_b), "now": now},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO organizations
                        (id, tenant_id, name, slug, status, created_at)
                    VALUES
                        (CAST(:org AS uuid), CAST(:tenant AS uuid),
                         'Other Tenant Org', 'other-org', 'active', :now)
                    """
                ),
                {"org": str(org_b), "tenant": str(tenant_b), "now": now},
            )

            # The single organization_id FK passes, but the composite tenant FK must fail.
            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(
                        text(
                            """
                            INSERT INTO schools
                                (id, tenant_id, organization_id, name, code, status, created_at)
                            VALUES
                                (CAST(:id AS uuid), CAST(:tenant AS uuid),
                                 CAST(:org AS uuid), 'Cross Tenant School', 'XTS',
                                 'setup', :now)
                            """
                        ),
                        {
                            "id": str(uuid4()),
                            "tenant": str(tenant_a),
                            "org": str(org_b),
                            "now": now,
                        },
                    )

            await connection.execute(
                text(
                    """
                    INSERT INTO schools
                        (id, tenant_id, organization_id, name, code, status, created_at)
                    VALUES
                        (CAST(:a AS uuid), CAST(:tenant_a AS uuid), NULL,
                         'School A', 'SA', 'active', :now),
                        (CAST(:b AS uuid), CAST(:tenant_b AS uuid), NULL,
                         'School B', 'SB', 'active', :now)
                    """
                ),
                {
                    "a": str(school_a),
                    "b": str(school_b),
                    "tenant_a": str(tenant_a),
                    "tenant_b": str(tenant_b),
                    "now": now,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO academic_years
                        (id, tenant_id, school_id, name, starts_on, ends_on, status, created_at)
                    VALUES
                        (CAST(:id AS uuid), CAST(:tenant AS uuid), CAST(:school AS uuid),
                         '1405', DATE '2026-03-21', DATE '2027-03-20', 'active', :now)
                    """
                ),
                {"id": str(year_b), "tenant": str(tenant_b), "school": str(school_b), "now": now},
            )

            # Each simple FK points to a real row; only the composite ownership check catches this.
            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(
                        text(
                            """
                            INSERT INTO class_groups
                                (id, tenant_id, school_id, academic_year_id, name, code,
                                 grade, status, created_at)
                            VALUES
                                (CAST(:id AS uuid), CAST(:tenant AS uuid),
                                 CAST(:school AS uuid), CAST(:year AS uuid),
                                 'Cross Linked', 'CL', '10', 'active', :now)
                            """
                        ),
                        {
                            "id": str(uuid4()),
                            "tenant": str(tenant_a),
                            "school": str(school_a),
                            "year": str(year_b),
                            "now": now,
                        },
                    )

    await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_rejects_invalid_role_binding_scope_shapes() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is not configured.")

    engine = create_async_engine(database_url)
    tenant_a, tenant_b = uuid4(), uuid4()
    user_id, role_id = uuid4(), uuid4()
    now = datetime.now(timezone.utc)

    async with engine.connect() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    """
                    INSERT INTO tenants
                        (id, slug, name, status, timezone, locale, created_at)
                    VALUES
                        (CAST(:a AS uuid), :slug_a, 'Scope A', 'active',
                         'Asia/Tehran', 'fa-IR', :now),
                        (CAST(:b AS uuid), :slug_b, 'Scope B', 'active',
                         'Asia/Tehran', 'fa-IR', :now)
                    """
                ),
                {
                    "a": str(tenant_a), "b": str(tenant_b),
                    "slug_a": f"scope-a-{tenant_a.hex[:8]}",
                    "slug_b": f"scope-b-{tenant_b.hex[:8]}",
                    "now": now,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO users
                        (id, email, display_name, password_hash, status,
                         failed_login_attempts, created_at, updated_at)
                    VALUES
                        (CAST(:id AS uuid), :email, 'Scope Test', 'not-used',
                         'active', 0, :now, :now)
                    """
                ),
                {"id": str(user_id), "email": f"{user_id.hex}@example.test", "now": now},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO roles
                        (id, tenant_id, key, name, is_system, created_at)
                    VALUES
                        (CAST(:id AS uuid), CAST(:tenant AS uuid),
                         'scope_test', 'Scope Test', FALSE, :now)
                    """
                ),
                {"id": str(role_id), "tenant": str(tenant_a), "now": now},
            )

            # Unknown scope names are rejected by the exact allow-list in the DB constraint.
            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(
                        text(
                            """
                            INSERT INTO role_bindings
                                (id, user_id, role_id, tenant_id, scope_type, scope_id, created_at)
                            VALUES
                                (CAST(:id AS uuid), CAST(:user AS uuid), CAST(:role AS uuid),
                                 CAST(:tenant AS uuid), 'department', CAST(:scope AS uuid), :now)
                            """
                        ),
                        {
                            "id": str(uuid4()), "user": str(user_id), "role": str(role_id),
                            "tenant": str(tenant_a), "scope": str(uuid4()), "now": now,
                        },
                    )

            # A tenant binding must use the same identifier for tenant_id and scope_id.
            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(
                        text(
                            """
                            INSERT INTO role_bindings
                                (id, user_id, role_id, tenant_id, scope_type, scope_id, created_at)
                            VALUES
                                (CAST(:id AS uuid), CAST(:user AS uuid), CAST(:role AS uuid),
                                 CAST(:tenant_a AS uuid), 'tenant', CAST(:tenant_b AS uuid), :now)
                            """
                        ),
                        {
                            "id": str(uuid4()), "user": str(user_id), "role": str(role_id),
                            "tenant_a": str(tenant_a), "tenant_b": str(tenant_b), "now": now,
                        },
                    )

    await engine.dispose()



@pytest.mark.asyncio
async def test_postgres_enforces_pairing_scope_and_one_open_invite_per_target() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is not configured.")

    engine = create_async_engine(database_url)
    tenant_id, user_id, role_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    target_telegram_id = 8_123_456_789

    async with engine.connect() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    """
                    INSERT INTO tenants
                        (id, slug, name, status, timezone, locale, created_at)
                    VALUES
                        (CAST(:id AS uuid), :slug, 'Pairing Scope Tenant', 'active',
                         'Asia/Tehran', 'fa-IR', :now)
                    """
                ),
                {
                    "id": str(tenant_id),
                    "slug": f"pairing-scope-{tenant_id.hex[:8]}",
                    "now": now,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO users
                        (id, email, display_name, password_hash, status,
                         failed_login_attempts, created_at, updated_at)
                    VALUES
                        (CAST(:id AS uuid), :email, 'Pairing Test', NULL,
                         'pending', 0, :now, :now)
                    """
                ),
                {
                    "id": str(user_id),
                    "email": f"{user_id.hex}@pairing.example.test",
                    "now": now,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO roles
                        (id, tenant_id, key, name, is_system, created_at)
                    VALUES
                        (CAST(:id AS uuid), CAST(:tenant AS uuid),
                         'tenant_admin_test', 'Tenant Admin Test', TRUE, :now)
                    """
                ),
                {"id": str(role_id), "tenant": str(tenant_id), "now": now},
            )

            valid_values = {
                "id": str(uuid4()),
                "user": str(user_id),
                "target": target_telegram_id,
                "token": uuid4().hex + uuid4().hex,
                "role": str(role_id),
                "tenant": str(tenant_id),
                "scope": str(tenant_id),
                "created_by": str(user_id),
                "created": now,
                "expires": now + timedelta(minutes=15),
            }
            await connection.execute(
                text(
                    """
                    INSERT INTO admin_pairing_tokens
                        (id, user_id, target_telegram_user_id, token_hash, role_id,
                         tenant_id, scope_type, scope_id, created_by_id, expires_at,
                         consumed_at, revoked_at, created_at)
                    VALUES
                        (CAST(:id AS uuid), CAST(:user AS uuid), :target, :token,
                         CAST(:role AS uuid), CAST(:tenant AS uuid), 'tenant',
                         CAST(:scope AS uuid), CAST(:created_by AS uuid), :expires,
                         NULL, NULL, :created)
                    """
                ),
                valid_values,
            )

            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    duplicate_values = dict(valid_values)
                    duplicate_values["id"] = str(uuid4())
                    duplicate_values["token"] = uuid4().hex + uuid4().hex
                    await connection.execute(
                        text(
                            """
                            INSERT INTO admin_pairing_tokens
                                (id, user_id, target_telegram_user_id, token_hash, role_id,
                                 tenant_id, scope_type, scope_id, created_by_id, expires_at,
                                 consumed_at, revoked_at, created_at)
                            VALUES
                                (CAST(:id AS uuid), CAST(:user AS uuid), :target, :token,
                                 CAST(:role AS uuid), CAST(:tenant AS uuid), 'tenant',
                                 CAST(:scope AS uuid), CAST(:created_by AS uuid), :expires,
                                 NULL, NULL, :created)
                            """
                        ),
                        duplicate_values,
                    )

            with pytest.raises(IntegrityError):
                async with connection.begin_nested():
                    invalid_values = dict(valid_values)
                    invalid_values["id"] = str(uuid4())
                    invalid_values["target"] = target_telegram_id + 1
                    invalid_values["token"] = uuid4().hex + uuid4().hex
                    invalid_values["scope"] = str(uuid4())
                    await connection.execute(
                        text(
                            """
                            INSERT INTO admin_pairing_tokens
                                (id, user_id, target_telegram_user_id, token_hash, role_id,
                                 tenant_id, scope_type, scope_id, created_by_id, expires_at,
                                 consumed_at, revoked_at, created_at)
                            VALUES
                                (CAST(:id AS uuid), CAST(:user AS uuid), :target, :token,
                                 CAST(:role AS uuid), CAST(:tenant AS uuid), 'tenant',
                                 CAST(:scope AS uuid), CAST(:created_by AS uuid), :expires,
                                 NULL, NULL, :created)
                            """
                        ),
                        invalid_values,
                    )

    await engine.dispose()
