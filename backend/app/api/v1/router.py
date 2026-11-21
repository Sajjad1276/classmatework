from fastapi import APIRouter

from app.api.v1.health import router as health_router
from app.modules.identity.router import auth_router, current_user_router
from app.modules.tenants.router import router as tenant_router

api_router = APIRouter()
api_router.include_router(health_router, prefix="/health", tags=["health"])
api_router.include_router(auth_router, prefix="/auth", tags=["identity"])
api_router.include_router(current_user_router, tags=["identity-current"])
api_router.include_router(tenant_router, tags=["tenants-and-academic"])
