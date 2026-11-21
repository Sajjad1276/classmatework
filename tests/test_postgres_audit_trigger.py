import os
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_postgres_audit_trigger_rejects_update_and_delete() -> None:
    database_url = os.getenv("TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("TEST_POSTGRES_URL is not configured.")

    engine = create_async_engine(database_url)
    event_id = str(uuid4())
    update_rejected = False
    delete_rejected = False

    async with engine.connect() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    """
                    INSERT INTO audit_events (
                        id, tenant_id, actor_id, event_type, aggregate_type,
                        aggregate_id, correlation_id, event_data, created_at
                    ) VALUES (
                        CAST(:event_id AS uuid), NULL, NULL, 'ci.audit.trigger_test',
                        'ci', NULL, 'ci-trigger-test', '{}'::json, CURRENT_TIMESTAMP
                    )
                    """
                ),
                {"event_id": event_id},
            )

            try:
                async with connection.begin_nested():
                    await connection.execute(
                        text(
                            "UPDATE audit_events SET event_type = 'tampered' "
                            "WHERE id = CAST(:event_id AS uuid)"
                        ),
                        {"event_id": event_id},
                    )
            except DBAPIError:
                update_rejected = True

            try:
                async with connection.begin_nested():
                    await connection.execute(
                        text("DELETE FROM audit_events WHERE id = CAST(:event_id AS uuid)"),
                        {"event_id": event_id},
                    )
            except DBAPIError:
                delete_rejected = True

    await engine.dispose()
    assert update_rejected, "PostgreSQL accepted an UPDATE against audit_events."
    assert delete_rejected, "PostgreSQL accepted a DELETE against audit_events."
