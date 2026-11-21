from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    display_name: str
    status: str


class TenantMembershipRead(BaseModel):
    tenant_id: UUID
    tenant_name: str
    tenant_slug: str
    membership_status: str


class MeRead(UserRead):
    memberships: list[TenantMembershipRead]


class LoginResponse(BaseModel):
    user: UserRead
    expires_at: datetime
