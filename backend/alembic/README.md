# Database migrations

The initial phase-one migration creates the tenant, identity, session, scoped RBAC,
academic structure, and append-only audit tables.

Apply migrations after provisioning PostgreSQL:

    alembic -c backend/alembic.ini upgrade head

The first platform owner must be provisioned interactively after migrations:

    PYTHONPATH=backend python -m app.cli create-platform-owner

Never run autogenerate migrations against production without reviewing schema and data impact.
Audit immutability is enforced in PostgreSQL by a database trigger and in SQLAlchemy ORM
by model events. Any future privileged maintenance procedure must preserve the audit trail.
