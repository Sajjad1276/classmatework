from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.dependencies import get_db
from app.db.models import AuthSession, Tenant, TenantMembership, User
from app.modules.audit.service import record_event
from app.modules.identity.dependencies import Principal, get_current_principal, require_csrf
from app.modules.identity.passwords import (
    dummy_password_hash,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.modules.identity.schemas import (
    LoginRequest,
    LoginResponse,
    MeRead,
    TenantMembershipRead,
    UserRead,
)
from app.modules.identity.tokens import digest_secret, generate_secret

auth_router = APIRouter()
current_user_router = APIRouter()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _enforce_login_rate_limit(request: Request) -> None:
    redis = request.app.state.redis
    address = request.client.host if request.client else "unknown"
    key = f"classmatework:auth:login:{digest_secret(address)[:24]}"
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 60)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily unavailable.",
        ) from None
    if count > 15:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again later.",
        )


@auth_router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    await _enforce_login_rate_limit(request)
    now = datetime.now(UTC)
    user = (
        await db.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()

    password_valid = verify_password(
        user.password_hash if user is not None else dummy_password_hash(),
        payload.password,
    )
    if (
        user is None
        or user.status != "active"
        or (user.locked_until is not None and _utc(user.locked_until) > now)
        or not password_valid
    ):
        if user is not None:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= get_settings().login_max_attempts:
                user.locked_until = now + timedelta(minutes=get_settings().login_lockout_minutes)
            await record_event(
                db,
                event_type="auth.login.failed",
                actor_id=user.id,
                event_data={"reason": "invalid_credentials_or_locked"},
            )
        else:
            await record_event(
                db,
                event_type="auth.login.failed",
                actor_id=None,
                event_data={"reason": "invalid_credentials"},
            )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now

    raw_token = generate_secret()
    raw_csrf = generate_secret()
    settings = get_settings()
    expires_at = now + timedelta(minutes=settings.session_ttl_minutes)
    auth_session = AuthSession(
        user_id=user.id,
        token_hash=digest_secret(raw_token),
        csrf_hash=digest_secret(raw_csrf),
        user_agent=request.headers.get("user-agent", "")[:512] or None,
        ip_address=request.client.host if request.client else None,
        expires_at=expires_at,
        last_seen_at=now,
    )
    db.add(auth_session)
    await db.flush()
    await record_event(
        db,
        event_type="auth.login.succeeded",
        actor_id=user.id,
        event_data={"session_id": str(auth_session.id)},
    )
    await db.commit()

    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        max_age=settings.session_ttl_minutes * 60,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=raw_csrf,
        max_age=settings.session_ttl_minutes * 60,
        httponly=False,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    return LoginResponse(user=UserRead.model_validate(user), expires_at=expires_at)


@current_user_router.get("/me", response_model=MeRead)
async def me(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> MeRead:
    result = await db.execute(
        select(TenantMembership, Tenant)
        .join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(
            TenantMembership.user_id == principal.user.id,
            TenantMembership.status == "active",
            Tenant.status == "active",
        )
    )
    memberships = [
        TenantMembershipRead(
            tenant_id=membership.tenant_id,
            tenant_name=tenant.name,
            tenant_slug=tenant.slug,
            membership_status=membership.status,
        )
        for membership, tenant in result.all()
    ]
    return MeRead(
        id=principal.user.id,
        email=principal.user.email,
        display_name=principal.user.display_name,
        status=principal.user.status,
        memberships=memberships,
    )


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await require_csrf(request, principal)
    principal.session.revoked_at = datetime.now(UTC)
    await record_event(
        db,
        event_type="auth.session.revoked",
        actor_id=principal.user.id,
        event_data={"session_id": str(principal.session.id)},
    )
    await db.commit()
    settings = get_settings()
    response.delete_cookie(
        settings.session_cookie_name, path="/", secure=settings.secure_cookies,
        httponly=True, samesite="lax",
    )
    response.delete_cookie(
        settings.csrf_cookie_name, path="/", secure=settings.secure_cookies,
        httponly=False, samesite="lax",
    )
    return response
