from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.dependencies import get_db
from app.db.models import Tenant
from app.modules.identity.dependencies import Principal, get_current_principal


async def get_active_tenant(
    _principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
    tenant_header: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> Tenant:
    if not tenant_header:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="X-Tenant-ID is required.")
    try:
        tenant_id = UUID(tenant_header)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid tenant context.") from None

    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id, Tenant.status == "active"))
    ).scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found.")
    return tenant
