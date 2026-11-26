from __future__ import annotations

import hashlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors import InvalidApplicationInput, ResourceConflict


async def get_class_join_code_secret(
    db: AsyncSession,
    *,
    class_id: int,
) -> dict | None:
    """Return the last issued code for a product class, from the admin DB."""
    if class_id <= 0:
        raise InvalidApplicationInput("شناسه کلاس معتبر نیست.")

    result = await db.execute(
        text(
            """
            SELECT class_id, join_code, join_code_hash, updated_at
            FROM class_join_code_secrets
            WHERE class_id = :class_id
            """
        ),
        {"class_id": class_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


async def store_class_join_code_secret(
    db: AsyncSession,
    *,
    class_id: int,
    code: str,
) -> dict:
    """Persist the displayable class code in the isolated ClassMate Work DB.

    ClassMate's product DB continues to store only the SHA-256 digest used by
    the student-facing membership flow. This table is the admin-only copy
    required to show the same code again without rotating it.
    """
    normalized_code = code.strip().upper()
    if class_id <= 0:
        raise InvalidApplicationInput("شناسه کلاس معتبر نیست.")
    if (
        not 8 <= len(normalized_code) <= 64
        or any(ch.isspace() for ch in normalized_code)
    ):
        raise InvalidApplicationInput("کد ورود کلاس معتبر نیست.")

    code_hash = hashlib.sha256(normalized_code.encode("utf-8")).hexdigest()
    duplicate = await db.execute(
        text(
            """
            SELECT class_id
            FROM class_join_code_secrets
            WHERE join_code_hash = :join_code_hash
              AND class_id <> :class_id
            """
        ),
        {"join_code_hash": code_hash, "class_id": class_id},
    )
    if duplicate.scalar_one_or_none() is not None:
        raise ResourceConflict("این کد قبلاً برای کلاس دیگری ثبت شده است.")

    result = await db.execute(
        text(
            """
            INSERT INTO class_join_code_secrets
                (class_id, join_code, join_code_hash, created_at, updated_at)
            VALUES
                (:class_id, :join_code, :join_code_hash, NOW(), NOW())
            ON CONFLICT (class_id)
            DO UPDATE SET
                join_code = EXCLUDED.join_code,
                join_code_hash = EXCLUDED.join_code_hash,
                updated_at = NOW()
            RETURNING class_id, join_code, join_code_hash, updated_at
            """
        ),
        {
            "class_id": class_id,
            "join_code": normalized_code,
            "join_code_hash": code_hash,
        },
    )
    return dict(result.mappings().one())
