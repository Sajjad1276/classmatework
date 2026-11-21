from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TenantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
    timezone: str = Field(default="Asia/Tehran", min_length=1, max_length=64)
    locale: str = Field(default="fa-IR", min_length=2, max_length=12)

    @field_validator("slug")
    @classmethod
    def normalize_slug(cls, value: str) -> str:
        return value.strip().lower()


class TenantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    name: str
    status: str
    timezone: str
    locale: str
    created_at: datetime


class SchoolCreate(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    code: str = Field(min_length=1, max_length=48)
    organization_id: UUID | None = None


class SchoolRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    organization_id: UUID | None
    name: str
    code: str
    status: str
    created_at: datetime


class AcademicYearCreate(BaseModel):
    school_id: UUID
    name: str = Field(min_length=2, max_length=64)
    starts_on: date
    ends_on: date

    @field_validator("ends_on")
    @classmethod
    def validate_dates(cls, value: date, info) -> date:
        start = info.data.get("starts_on")
        if start is not None and value <= start:
            raise ValueError("Academic year end must be after its start.")
        return value


class ClassCreate(BaseModel):
    school_id: UUID
    academic_year_id: UUID
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=48)
    grade: str = Field(min_length=1, max_length=48)


class SubjectCreate(BaseModel):
    school_id: UUID
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=48)


class AcademicResourceRead(BaseModel):
    id: UUID
    tenant_id: UUID
    school_id: UUID
    name: str
    code: str
    status: str
    created_at: datetime


class AcademicYearRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    school_id: UUID
    name: str
    starts_on: date
    ends_on: date
    status: str
    created_at: datetime


class ClassRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    school_id: UUID
    academic_year_id: UUID
    name: str
    code: str
    grade: str
    status: str
    created_at: datetime
