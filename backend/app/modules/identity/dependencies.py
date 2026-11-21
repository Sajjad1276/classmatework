from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from secrets import compare_digest

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.dependencies import get_db
from app.db.models import AuthSession, User
from app.modules.identity.tokens import digest_secret


@dataclass(frozen=True)
class Principal:
    user: User
    session: AuthSession


async def get_current_principal(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Principal:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")

    result = await db.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(
            AuthSession.token_hash == digest_secret(token),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > datetime.now(UTC),
            User.status == "active",
        )
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    session, user = row
    return Principal(user=user, session=session)


async def require_csrf(request: Request, principal: Principal) -> None:
    settings = get_settings()
    cookie_value = request.cookies.get(settings.csrf_cookie_name, "")
    header_value = request.headers.get("X-CSRF-Token", "")
    valid = (
        bool(cookie_value)
        and bool(header_value)
        and compare_digest(cookie_value, header_value)
        and compare_digest(digest_secret(cookie_value), principal.session.csrf_hash)
    )
    if not valid:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed.")
